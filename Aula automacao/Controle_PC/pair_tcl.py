import asyncio
import sys
from androidtvremote2 import AndroidTVRemote

async def pair(ip):
    client_name = "Cortana Assistant"
    certfile = "memory/tcl_cert.pem"
    keyfile = "memory/tcl_key.pem"
    
    # Garante que a pasta memory existe
    import os
    os.makedirs("memory", exist_ok=True)
    
    # Inicializa o remote
    remote = AndroidTVRemote(
        client_name=client_name,
        certfile=certfile,
        keyfile=keyfile,
        host=ip
    )

    print(f"\n--- Iniciando Pareamento com a TV em {ip} ---")
    
    try:
        # Gera certificados se não existirem
        print("[INFO] Verificando certificados...")
        from androidtvremote2.certificate_generator import generate_selfsigned_cert
        if not os.path.exists(certfile) or not os.path.exists(keyfile):
            cert_bytes, key_bytes = generate_selfsigned_cert(client_name)
            with open(certfile, "wb") as f:
                f.write(cert_bytes)
            with open(keyfile, "wb") as f:
                f.write(key_bytes)
            print("[INFO] Novos certificados gerados.")

        # Inicia o pareamento
        print("[INFO] Solicitando pareamento à TV...")
        await remote.async_start_pairing()
        
        # Pede o PIN e limpa espaços
        pin = input("\nDigite o código de 6 dígitos mostrado na TV: ").strip()
        
        # Finaliza o pareamento
        print("[INFO] Enviando código e finalizando...")
        # A função da lib retorna None em sucesso e lança exceção em erro
        await remote.async_finish_pairing(pin)
        print("\n[SUCESSO] Pareamento concluído com sucesso!")
        
        # Testa conexão
        print("[INFO] Testando conexão...")
        await remote.async_connect()
        print("[SUCESSO] TV conectada. Agora a Cortana já pode controlá-la!")

    except Exception as e:
        import traceback
        print("\n[ERRO DETALHADO]")
        traceback.print_exc()
        print(f"\nFalha: {e}")
    finally:
        remote.disconnect()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python pair_tcl.py <IP_DA_TV>")
        sys.exit(1)
    
    ip_tv = sys.argv[1]
    asyncio.run(pair(ip_tv))
