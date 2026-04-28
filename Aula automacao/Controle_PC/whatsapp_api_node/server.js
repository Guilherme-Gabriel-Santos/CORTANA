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

// Agenda manual de aliases: nome -> numero (editavel pelo usuario via API ou direto no arquivo)
const aliasesPath = path.join(__dirname, 'contacts.json');
let aliases = {};
function loadAliases() {
    try {
        if (fs.existsSync(aliasesPath)) {
            aliases = JSON.parse(fs.readFileSync(aliasesPath, 'utf-8')) || {};
        } else {
            aliases = {};
            fs.writeFileSync(aliasesPath, '{}\n', 'utf-8');
        }
    } catch (e) {
        console.error('Erro lendo contacts.json:', e.message);
        aliases = {};
    }
}
function saveAliases() {
    try {
        fs.writeFileSync(aliasesPath, JSON.stringify(aliases, null, 2) + '\n', 'utf-8');
    } catch (e) {
        console.error('Erro salvando contacts.json:', e.message);
    }
}
loadAliases();
// Recarrega se o arquivo for editado manualmente
fs.watchFile(aliasesPath, { interval: 2000 }, () => {
    loadAliases();
    console.log(`contacts.json recarregado (${Object.keys(aliases).length} aliases).`);
});

let sock = null;
let qrCode = null;
let isConnected = false;
let messageQueue = [];
let contactMap = {}; // pushName -> jid

// ───────── Helpers de contato ─────────
function normalize(s) {
    return (s || '')
        .toString()
        .toLowerCase()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .trim();
}

function numberToJid(num) {
    let clean = String(num || '').replace(/\D/g, '');
    if (clean.length < 8) return null;
    if (clean.length >= 10 && !clean.startsWith('55')) {
        clean = '55' + clean;
    }
    return `${clean}@s.whatsapp.net`;
}

function resolveJid(contact) {
    const needle = normalize(contact);
    if (!needle) return null;

    const sources = [];

    // 1. Aliases manuais (contacts.json) - prioridade maxima
    for (const [name, number] of Object.entries(aliases)) {
        const jid = numberToJid(number);
        if (jid) sources.push({ name, jid, priority: 1 });
    }

    // 2. pushName aprendidos dinamicamente
    for (const [name, jid] of Object.entries(contactMap)) {
        sources.push({ name, jid, priority: 2 });
    }

    // 3. Store do Baileys (contatos sincronizados)
    if (store.contacts) {
        for (const c of Object.values(store.contacts)) {
            const names = [c.name, c.verifiedName, c.notify].filter(Boolean);
            for (const n of names) {
                sources.push({ name: n, jid: c.id, priority: 3 });
            }
        }
    }

    // Match exato primeiro, respeitando prioridade das fontes
    for (const prio of [1, 2, 3]) {
        const exact = sources.find(s => s.priority === prio && normalize(s.name) === needle);
        if (exact) return exact.jid;
    }

    // Depois match parcial (contem), respeitando prioridade
    for (const prio of [1, 2, 3]) {
        const partial = sources.find(s => {
            if (s.priority !== prio) return false;
            const hay = normalize(s.name);
            return hay.includes(needle) || needle.includes(hay);
        });
        if (partial) return partial.jid;
    }

    // Ultimo recurso: trata o input como numero de telefone
    return numberToJid(contact);
}

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
        contacts_known: Object.keys(contactMap).length,
        aliases_count: Object.keys(aliases).length
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
        const jid = resolveJid(contact);
        if (!jid) {
            return res.status(400).json({
                success: false,
                message: `Não encontrei o contato "${contact}". Salve com "salva tal número como tal nome" ou passe o número direto.`
            });
        }

        await sock.sendMessage(jid, { text: message });
        res.json({ success: true, jid });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// Agenda manual (contacts.json)
app.get('/contacts', (req, res) => {
    res.json({ aliases, count: Object.keys(aliases).length });
});

app.post('/contacts', (req, res) => {
    const { name, number } = req.body || {};
    if (!name || !number) {
        return res.status(400).json({ success: false, message: 'Informe "name" e "number".' });
    }
    const clean = String(number).replace(/\D/g, '');
    if (clean.length < 8) {
        return res.status(400).json({ success: false, message: 'Numero invalido.' });
    }
    const key = normalize(name);
    aliases[key] = clean;
    saveAliases();
    res.json({ success: true, name: key, number: clean, total: Object.keys(aliases).length });
});

app.delete('/contacts/:name', (req, res) => {
    const key = normalize(req.params.name);
    if (!(key in aliases)) {
        return res.status(404).json({ success: false, message: 'Alias nao encontrado.' });
    }
    delete aliases[key];
    saveAliases();
    res.json({ success: true, removed: key, total: Object.keys(aliases).length });
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
    console.log(`Aliases carregados: ${Object.keys(aliases).length}`);
    connectToWhatsApp();
});
