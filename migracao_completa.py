import json
import sqlite3
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from datetime import datetime

# 1. Carrega a senha do banco (Supabase) do arquivo .env
load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    print("❌ ERRO: Arquivo .env não encontrado ou sem a DATABASE_URL.")
    print("Crie um arquivo chamado .env e coloque: DATABASE_URL=\"postgresql://...\"")
    exit()

# Cria conexão com a Nuvem
engine = create_engine(DB_URL)

def migrar_etiquetas():
    print("\n📦 Iniciando migração de ETIQUETAS (dados.json)...")
    
    if not os.path.exists('dados.json'):
        print("⚠️  Arquivo dados.json não encontrado. Pulando.")
        return

    try:
        with open('dados.json', 'r', encoding='utf-8') as f:
            dados = json.load(f)

        count = 0
        with engine.begin() as conn: # 'begin' abre transação automática
            for data_dia, info in dados.items():
                if 'entradas' in info:
                    for entrada in info['entradas']:
                        # Monta a data completa: "2026-01-13 14:30:00"
                        data_hora_str = f"{data_dia} {entrada['horario']}"
                        valor = entrada['valor']
                        
                        # Insere no Supabase
                        conn.execute(text("""
                            INSERT INTO etiquetas_log (data_hora, quantidade, usuario)
                            VALUES (:dh, :qtd, 'Migracao_Antigo')
                        """), {"dh": data_hora_str, "qtd": valor})
                        count += 1
        print(f"✅ Sucesso! {count} registros de etiquetas migrados.")
        
    except Exception as e:
        print(f"❌ Erro ao migrar etiquetas: {e}")

def migrar_estoque_sqlite():
    print("\n📦 Iniciando migração de HISTÓRICO/QUALIDADE (estoque.db)...")
    
    if not os.path.exists('estoque.db'):
        print("⚠️  Arquivo estoque.db não encontrado. Pulando.")
        return

    try:
        # Conecta no banco antigo local (SQLite)
        sqlite_conn = sqlite3.connect('estoque.db')
        c = sqlite_conn.cursor()
        
        # Seleciona dados da tabela antiga 'testes'
        # A estrutura antiga era: id, data_registro, setor, sku, loja, pedido, resultado, obs
        try:
            c.execute("SELECT data_registro, setor, sku, resultado, obs FROM testes")
            rows = c.fetchall()
        except sqlite3.OperationalError:
            print("⚠️  Tabela 'testes' não encontrada no estoque.db. Pulando.")
            rows = []

        count = 0
        with engine.begin() as conn_pg:
            for row in rows:
                data_reg, setor, sku, resultado, obs = row
                
                # Tratamento de data (às vezes o SQLite salva estranho)
                if not data_reg:
                    data_reg = datetime.now()
                
                # Insere na nova tabela unificada 'estoque_movimento'
                conn_pg.execute(text("""
                    INSERT INTO estoque_movimento 
                    (data_registro, sku, tipo_movimento, quantidade, resultado, origem, obs)
                    VALUES (:dt, :sku, 'HISTORICO', 1, :res, :origem, :obs)
                """), {
                    "dt": data_reg,
                    "sku": sku,
                    "res": resultado,
                    "origem": setor, # 'atendimento' ou 'estoque' viram origem
                    "obs": obs
                })
                count += 1
                
        print(f"✅ Sucesso! {count} registros de histórico migrados.")
        sqlite_conn.close()

    except Exception as e:
        print(f"❌ Erro ao migrar estoque.db: {e}")

if __name__ == "__main__":
    print("🚀 INICIANDO MIGRAÇÃO PARA A NUVEM...")
    migrar_etiquetas()
    migrar_estoque_sqlite()
    print("\n🏁 Processo finalizado.")
