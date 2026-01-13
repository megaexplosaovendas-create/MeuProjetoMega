import os
import json
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from sqlalchemy import create_engine, text

# --- CONFIGURAÇÃO ---
app = Flask(__name__, static_folder='.')

# ⚠️ COLOQUE SUA CONEXÃO DO SUPABASE AQUI
DB_URL = "postgresql://postgres.SEU_USUARIO:SUA_SENHA@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
engine = create_engine(DB_URL)

def get_db():
    return engine.connect()

# ==============================================================================
# 1. ROTAS DE NAVEGAÇÃO (As telas do seu sistema)
# ==============================================================================

@app.route('/')
def menu_principal():
    return """
    <div style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h1>🚀 Sistema Integrado Mega</h1>
        <div style="display: flex; gap: 20px; justify-content: center; margin-top: 30px;">
            <a href="/etiquetas" style="padding: 20px; background: #2ecc71; color: white; text-decoration: none; border-radius: 8px;">🏷️ Contador de Etiquetas</a>
            <a href="/estoque" style="padding: 20px; background: #3498db; color: white; text-decoration: none; border-radius: 8px;">📦 Controle de Estoque/Qualidade</a>
            <a href="/bipagem" style="padding: 20px; background: #9b59b6; color: white; text-decoration: none; border-radius: 8px;">🔍 Bipagem (Conferência)</a>
        </div>
    </div>
    """

@app.route('/etiquetas')
def view_etiquetas():
    return send_from_directory('.', 'Controle_Etiquetas.html')

@app.route('/estoque')
def view_estoque():
    # Pode usar o Controle_de_Estoque.html ou VerificadorEstoque.html aqui
    return send_from_directory('.', 'Controle_de_Estoque.html') 

@app.route('/bipagem')
def view_bipagem():
    return send_from_directory('.', 'index.html')

# ==============================================================================
# 2. API UNIFICADA (Backend que fala com o Banco)
# ==============================================================================

# --- MÓDULO ETIQUETAS ---
@app.route('/dados', methods=['GET'])
def api_etiquetas_get():
    """Lê o banco SQL e transforma no JSON que seu HTML antigo espera"""
    with get_db() as conn:
        rows = conn.execute(text("SELECT * FROM etiquetas_log ORDER BY data_hora ASC")).fetchall()
    
    dados_json = {}
    for row in rows:
        dt = row.data_hora.strftime('%Y-%m-%d')
        hr = row.data_hora.strftime('%H:%M:%S')
        if dt not in dados_json: dados_json[dt] = {"entradas": [], "total": 0}
        dados_json[dt]["entradas"].append({"horario": hr, "valor": row.quantidade})
        dados_json[dt]["total"] += row.quantidade
    return jsonify(dados_json)

@app.route('/dados', methods=['POST'])
def api_etiquetas_post():
    """Recebe o JSON do HTML e salva a ÚLTIMA entrada no SQL"""
    dados = request.json
    hoje = datetime.now().strftime('%Y-%m-%d')
    if hoje in dados and dados[hoje]['entradas']:
        ultimo = dados[hoje]['entradas'][-1]
        with get_db() as conn:
            conn.execute(text("INSERT INTO etiquetas_log (quantidade) VALUES (:q)"), {"q": ultimo['valor']})
            conn.commit()
    return jsonify({"status": "ok"})

# --- MÓDULO ESTOQUE & QUALIDADE ---
@app.route('/api/estoque', methods=['GET'])
def api_estoque_get():
    """Retorna saldo de estoque agrupado por SKU"""
    with get_db() as conn:
        # Soma entradas - saídas (lógica simplificada)
        sql = """
            SELECT sku, 
                   SUM(CASE WHEN tipo_movimento = 'ENTRADA' THEN quantidade 
                            WHEN tipo_movimento = 'SAIDA' THEN -quantidade 
                            ELSE 0 END) as saldo
            FROM estoque_movimento
            GROUP BY sku
        """
        rows = conn.execute(text(sql)).fetchall()
    
    lista = [{"id": r.sku, "display": r.sku, "qty": r.saldo, "aliases": []} for r in rows]
    return jsonify(lista)

@app.route('/api/movimentar', methods=['POST'])
def api_movimentar():
    """Registra Entrada, Saída ou Qualidade"""
    d = request.json
    # Espera receber: { sku: "X", tipo: "SAIDA", qtd: 1, obs: "..." }
    with get_db() as conn:
        conn.execute(text("""
            INSERT INTO estoque_movimento (sku, tipo_movimento, quantidade, obs, origem)
            VALUES (:sku, :tipo, :qtd, :obs, 'Sistema Web')
        """), {"sku": d.get('sku'), "tipo": d.get('tipo', 'SAIDA'), "qtd": d.get('qtd', 1), "obs": d.get('obs', '')})
        conn.commit()
    return jsonify({"msg": "Movimento salvo"})

# --- MÓDULO BIPAGEM (CONFERÊNCIA) ---
@app.route('/api/salvar_conferencia', methods=['POST'])
def api_salvar_conferencia():
    """Recebe os dados do index.html e salva no banco"""
    dados = request.json
    # Espera lista de bipes: { motorista: "...", bipes: [{code: "ABC", status: "OK"}] }
    
    with get_db() as conn:
        for item in dados.get('bipes', []):
            conn.execute(text("""
                INSERT INTO estoque_movimento (sku, tipo_movimento, resultado, obs, origem)
                VALUES (:sku, 'CONFERENCIA', :res, :mot, 'Bipagem Motorista')
            """), {
                "sku": item['code'], 
                "res": item['msg'], 
                "mot": f"Motorista: {dados.get('motorista')}"
            })
        conn.commit()
    return jsonify({"msg": "Conferência salva na nuvem!"})

# ==============================================================================
# 3. BOT UPSELLER (Recebimento Automático de Excel)
# ==============================================================================

@app.route('/api/bot/upload_upseller', methods=['POST'])
def bot_upload_excel():
    """
    Esta rota espera receber um arquivo .xlsx enviado pelo seu Bot Python.
    Ela lê o Excel, atualiza o estoque no banco e registra a importação.
    """
    if 'file' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    
    arquivo = request.files['file']
    
    try:
        # Lê o Excel usando Pandas
        df = pd.read_excel(arquivo)
        
        # SUPONDO que o Excel do Upseller tenha colunas: 'SKU' e 'Quantidade'
        # Ajuste os nomes das colunas conforme seu arquivo real
        total_linhas = 0
        with get_db() as conn:
            for index, row in df.iterrows():
                sku = row.get('SKU Mestre', row.get('SKU', 'DESCONHECIDO')) # Tenta achar coluna SKU
                qtd = row.get('Estoque', row.get('Quantidade', 0))
                
                # Insere como uma atualização de estoque (tipo 'IMPORTACAO_BOT')
                conn.execute(text("""
                    INSERT INTO estoque_movimento (sku, tipo_movimento, quantidade, origem)
                    VALUES (:sku, 'IMPORTACAO_BOT', :qtd, 'Bot Upseller')
                """), {"sku": sku, "qtd": qtd})
                total_linhas += 1
            
            # Registra Log
            conn.execute(text("INSERT INTO importacoes_upseller (arquivo_nome, total_linhas, status) VALUES (:n, :t, 'OK')"),
                         {"n": arquivo.filename, "t": total_linhas})
            conn.commit()
            
        return jsonify({"msg": f"Processado com sucesso. {total_linhas} linhas importadas."})
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
