const { 
    default: makeWASocket, 
    useMultiFileAuthState, 
    DisconnectReason,
    fetchLatestBaileysVersion,
    makeCacheableSignalKeyStore,
    makeInMemoryStore
} = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const P = require('pino');
const express = require('express');
const cors = require('cors');
const qrcode_terminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const fs = require('fs');
const path = require('path');

const logger = P({ level: 'info' });
const app = express();
app.use(express.json());
app.use(cors());

// Store para persistir contatos e conversas
const store = makeInMemoryStore({ logger });
const storePath = './baileys_store.json';
try {
    store.readFromFile(storePath);
} catch (e) {
    console.log('Criando novo arquivo de store de contatos...');
}
// Salva o store a cada 10 segundos
setInterval(() => {
    try {
        store.writeToFile(storePath);
    } catch (e) {}
}, 10000);

let sock = null;
let qrCode = null;
let isConnected = false;
let messageQueue = [];
let contactMap = {}; // pushName -> jid

async function connectToWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');
    const { version, isLatest } = await fetchLatestBaileysVersion();
    
    console.log(`Usando Baileys v${version.join('.')}${isLatest ? ' (latest)' : ''}`);

    sock = makeWASocket({
        version,
        logger,
        printQRInTerminal: false,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        generateHighQualityLinkPreview: true,
    });

    // Vincula o store ao socket para sincronização automática
    store.bind(sock.ev);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr) {
            qrCode = qr;
            console.log('--- SCAN QR CODE ---');
            qrcode_terminal.generate(qr, { small: true });
        }

        if (connection === 'close') {
            isConnected = false;
            const shouldReconnect = (lastDisconnect.error instanceof Boom) ? 
                lastDisconnect.error.output.statusCode !== DisconnectReason.loggedOut : true;
            console.log('Conexão fechada devido a ', lastDisconnect.error, ', reconectando: ', shouldReconnect);
            if (shouldReconnect) {
                connectToWhatsApp();
            }
        } else if (connection === 'open') {
            isConnected = true;
            qrCode = null;
            console.log('Conexão aberta com sucesso!');
        }
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('messages.upsert', async m => {
        if (m.type === 'notify') {
            for (const msg of m.messages) {
                if (!msg.key.fromMe && msg.message) {
                    const jid = msg.key.remoteJid;
                    const pushName = msg.pushName;
                    const text = msg.message.conversation || 
                                 msg.message.extendedTextMessage?.text || 
                                 (msg.message.imageMessage ? '[Imagem]' : '[Outra mídia]');
                    
                    if (pushName) {
                        contactMap[pushName.toLowerCase()] = jid;
                    }

                    if (text) {
                        const contactLabel = pushName || jid.split('@')[0];
                        console.log(`Nova mensagem de ${contactLabel}: ${text}`);
                        messageQueue.push({
                            id: msg.key.id,
                            contact: contactLabel,
                            text: text,
                            timestamp: new Date().toLocaleTimeString()
                        });
                    }
                }
            }
        }
    });
}

// REST API Endpoints
app.get('/status', (req, res) => {
    res.json({
        connected: isConnected,
        qr: qrCode ? 'available' : null,
        session_active: !!sock,
        controller_active: true,
        contacts_known: Object.keys(contactMap).length
    });
});

app.get('/qr', async (req, res) => {
    if (isConnected) return res.send('<h1>WhatsApp já está conectado!</h1>');
    if (!qrCode) return res.send('<h1>Gerando QR Code... Recarregue em instantes.</h1>');
    
    try {
        const qrImage = await QRCode.toDataURL(qrCode);
        res.send(`
            <html>
                <body style="background: #000; color: #0f0; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: monospace; text-align: center;">
                    <h1>ESCANEIE O QR CODE - CORTANA</h1>
                    <div style="background: #fff; padding: 20px; border-radius: 10px;">
                        <img src="${qrImage}" style="width: 300px; height: 300px;" />
                    </div>
                    <p style="margin-top: 20px;">O QR Code também foi impresso no terminal/logs.</p>
                    <script>setTimeout(() => location.reload(), 20000);</script>
                </body>
            </html>
        `);
    } catch (err) {
        res.status(500).send('Erro ao gerar imagem do QR Code');
    }
});

app.post('/send', async (req, res) => {
    const { contact, message } = req.body;
    if (!isConnected) return res.status(503).json({ success: false, message: 'WhatsApp não conectado' });
    
    try {
        let jid = null;
        const nameKey = contact.toLowerCase();

        // 1. Tenta resolver por pushName no mapa temporário (mais recente)
        if (contactMap[nameKey]) {
            jid = contactMap[nameKey];
        } 
        
        // 2. Tenta resolver no Store (contatos sincronizados/salvos)
        if (!jid && store.contacts) {
            const found = Object.values(store.contacts).find(c => 
                (c.name && c.name.toLowerCase() === nameKey) || 
                (c.verifiedName && c.verifiedName.toLowerCase() === nameKey) ||
                (c.notify && c.notify.toLowerCase() === nameKey)
            );
            if (found) jid = found.id;
        }

        // 3. Se não achou por nome, tenta tratar como número
        if (!jid) {
            let cleanContact = contact.replace(/\D/g, '');
            if (cleanContact.length >= 8) {
                if (cleanContact.length >= 10 && !cleanContact.startsWith('55')) {
                    cleanContact = '55' + cleanContact;
                }
                jid = `${cleanContact}@s.whatsapp.net`;
            }
        }

        if (!jid) {
            return res.status(400).json({ 
                success: false, 
                message: `Não encontrei o contato "${contact}". Tente enviar pelo número de telefone uma vez para eu aprender o nome.` 
            });
        }

        await sock.sendMessage(jid, { text: message });
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

app.get('/messages/new/agent', (req, res) => {
    const messages = [...messageQueue];
    messageQueue = [];
    res.json({ messages });
});

const PORT = 5050;
app.listen(PORT, () => {
    console.log(`WhatsApp API Server rodando na porta ${PORT}`);
    console.log(`Acesse http://localhost:${PORT}/qr para o QR Code`);
    connectToWhatsApp();
});
