import os
import json
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from sqlalchemy import create_engine, text
from dotenv import load_dotenv  # Biblioteca de segurança

# ==============================================================================
# 1. CONFIGURAÇÃO DE SEGURANÇA E BANCO DE DADOS
# ==============================================================================

# Carrega as variáveis do arquivo .env (apenas quando rodar no PC local)
load_dotenv()

app = Flask(__name__, static_folder='.')

# Busca a URL do banco nas variáveis de ambiente (Segurança Máxima)
# No Render, isso virá da configuração do site. No PC, virá do arquivo .env
DB_URL = os.getenv("DATABASE_URL")

# Verificação de segurança para não rodar sem banco
if not DB_URL:
    raise ValueError("❌ ERRO CRÍTICO: A variável 'DATABASE_URL' não foi encontrada. "
                     "Crie o arquivo .env (local) ou configure as Environment Variables (Render).")

# Correção para compatibilidade do Render com SQLAlchemy
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

# Cria o motor de conexão
engine = create_engine(DB_URL)

def get_db():
    """Abre uma conexão temporária com o banco"""
    return engine.connect()

# ==============================================================================
# 2. ROTAS DE NAVEGAÇÃO (Páginas HTML)
# ==============================================================================

@app.route('/')
def menu_principal():
    """Carrega o Menu Inicial (Dashboard)"""
    return send_from_directory('.', 'index.html')

@app.route('/bipagem')
def view_bipagem():
    """Carrega o Bipador (Antigo index.html)"""
    # Certifique-se de ter renomeado o antigo index.html para bipagem.html
    return send_from_directory('.', 'bipagem.html')

@app.route('/estoque')
def view_estoque():
    """Carrega o Controle de Estoque"""
    return send_from_directory('.', 'Controle_de_Estoque.html')

@app.route('/etiquetas')
def view_etiquetas():
    """Carrega o Contador de Etiquetas"""
    return send_from_directory('.', 'Controle_Etiquetas.html')

# ==============================================================================
# 3. API - MÓDULO ETIQUETAS
# ==============================================================================

@app.route('/dados', methods=['GET'])
def api_etiquetas_get():
    """Converte dados do SQL para o formato JSON do Front-end"""
    with get_db() as conn:
        result = conn.execute(text("SELECT * FROM etiquetas_log ORDER BY data_hora ASC"))
        rows = result.fetchall()

    dados_json = {}
    for row in rows:
        # row: (id, data_hora, quantidade, usuario)
        dt = row.data_hora.strftime('%Y-%m-%d')
        hr = row.data_hora.strftime('%H:%M:%S')
        
        if dt not in dados_json:
            dados_json[dt] = {"entradas": [], "total": 0}
            
        dados_json[dt]["entradas"].append({"horario": hr, "valor": row.quantidade})
        dados_json[dt]["total"] += row.quantidade

    return jsonify(dados_json)

@app.route('/dados', methods=['POST'])
def api_etiquetas_post():
    """Salva nova etiqueta no banco"""
    dados = request.json
    hoje = datetime.now().strftime('%Y-%m-%d')
    
    # Lógica para pegar apenas o último registro enviado pelo front antigo
    if hoje in dados and dados[hoje]['entradas']:
        ultimo = dados[hoje]['entradas'][-1]
        valor = ultimo['valor']
        
        with get_db() as conn:
            conn.execute(text("INSERT INTO etiquetas_log (quantidade, usuario) VALUES (:q, 'SistemaWeb')"), 
                         {"q": valor})
            conn.commit()
            
    return jsonify({"status": "ok"})

# ==============================================================================
# 4. API - MÓDULO ESTOQUE & QUALIDADE
# ==============================================================================

@app.route('/api/estoque', methods=['GET'])
def api_estoque_get():
    """Retorna o saldo de estoque agrupado"""
    with get_db() as conn:
        # Soma entradas e subtrai saídas
        sql = """
            SELECT sku, 
                   SUM(CASE WHEN tipo_movimento = 'ENTRADA' THEN quantidade 
                            WHEN tipo_movimento = 'SAIDA' THEN -quantidade 
                            ELSE 0 END) as saldo
            FROM estoque_movimento
            GROUP BY sku
        """
        rows = conn.execute(text(sql)).fetchall()
    
    # Formato que o HTML espera
    inventory = []
    for r in rows:
        inventory.append({
            "id": r.sku, 
            "display": r.sku, 
            "qty": r.saldo if r.saldo else 0, 
            "aliases": []
        })
        
    return jsonify(inventory)

@app.route('/api/movimentar', methods=['POST'])
def api_movimentar():
    """Registra movimentação manual (Entrada/Saída/Qualidade)"""
    d = request.json
    with get_db() as conn:
        conn.execute(text("""
            INSERT INTO estoque_movimento (sku, tipo_movimento, quantidade, obs, origem)
            VALUES (:sku, :tipo, :qtd, :obs, 'Manual Web')
        """), {
            "sku": d.get('sku'), 
            "tipo": d.get('tipo', 'SAIDA'), # Default SAIDA
            "qtd": d.get('qtd', 1), 
            "obs": d.get('obs', '')
        })
        conn.commit()
    return jsonify({"msg": "Salvo com sucesso"})

# ==============================================================================
# 5. API - MÓDULO BIPAGEM (CONFERÊNCIA)
# ==============================================================================

@app.route('/api/salvar_conferencia', methods=['POST'])
def api_salvar_conferencia():
    """Recebe o JSON do bipador e salva histórico"""
    dados = request.json
    motorista = dados.get('motorista', 'Desconhecido')
    bipes = dados.get('bipes', [])
    
    with get_db() as conn:
        for item in bipes:
            conn.execute(text("""
                INSERT INTO estoque_movimento (sku, tipo_movimento, resultado, obs, origem)
                VALUES (:sku, 'CONFERENCIA', :res, :mot, 'Bipagem App')
            """), {
                "sku": item['code'], 
                "res": item['msg'], 
                "mot": f"Motorista: {motorista}"
            })
        conn.commit()
        
    return jsonify({"msg": "Conferência salva na nuvem!"})

# ==============================================================================
# 6. API - BOT UPSELLER (UPLOAD DE EXCEL)
# ==============================================================================

@app.route('/api/bot/upload_upseller', methods=['POST'])
def bot_upload_excel():
    """Recebe arquivo Excel via Bot e atualiza estoque"""
    if 'file' not in request.files:
        return jsonify({"erro": "Arquivo não enviado"}), 400
    
    arquivo = request.files['file']
    
    try:
        df = pd.read_excel(arquivo)
        total = 0
        
        with get_db() as conn:
            # Insere registro de log da importação
            conn.execute(text("INSERT INTO importacoes_upseller (arquivo_nome, status) VALUES (:n, 'PROCESSANDO')"), 
                         {"n": arquivo.filename})
            conn.commit()

            # Itera sobre o Excel (ajuste as colunas conforme seu arquivo real)
            for _, row in df.iterrows():
                # Tenta pegar SKU ou Código
                sku = str(row.get('SKU', row.get('Código', 'SEM_SKU')))
                qtd = int(row.get('Estoque', row.get('Saldo', 0)))
                
                # Inserimos como um ajuste de inventário
                conn.execute(text("""
                    INSERT INTO estoque_movimento (sku, tipo_movimento, quantidade, origem, obs)
                    VALUES (:sku, 'IMPORTACAO_BOT', :qtd, 'Bot Upseller', 'Carga Automática')
                """), {"sku": sku, "qtd": qtd})
                total += 1
                
            conn.commit()
            
        return jsonify({"msg": f"Sucesso! {total} linhas processadas."})
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    # Roda o servidor na porta 5000
    app.run(host='0.0.0.0', port=5000, debug=True)
