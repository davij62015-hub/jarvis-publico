"""
Jarvis - Site Publico
Versao segura para hospedar na internet: so conversa com IA e gera imagens.
NAO tem nenhum comando de controle de PC (nada de abrir programas, desligar, etc).
"""

import os
import urllib.parse
from flask import Flask, request, jsonify, session
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "troque_essa_chave_em_producao")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
_cliente = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

SISTEMA = (
    "Voce e o Jarvis, assistente de IA criado por Samuca. "
    "Responda em portugues do Brasil, de forma clara e amigavel. "
    "Voce NAO tem controle sobre nenhum computador, e apenas um assistente de conversa e criacao de imagens."
)

PAGINA = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family: 'Segoe UI', Arial, sans-serif; background:#020a12; color:#e6f7ff; display:flex; height:100vh; }

.sidebar { width:260px; background:#03111c; border-right:1px solid #0a2a3a; padding:16px; display:flex; flex-direction:column; }
.logo-linha { display:flex; align-items:center; gap:10px; margin-bottom:20px; }
.menu-icone { display:flex; flex-direction:column; gap:4px; cursor:pointer; }
.menu-icone span { width:20px; height:2px; background:#00c8ff; display:block; }
.logo-hud { width:34px; height:34px; position:relative; }
.anel { position:absolute; border-radius:50%; border:2px solid #00c8ff; }
.anel1 { inset:0; } .anel2 { inset:6px; border-color:#5ef1ff; }
.novo-chat { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; border-radius:8px; padding:10px; text-align:left; cursor:pointer; margin-bottom:16px; }
.historico-lista { flex:1; overflow-y:auto; font-size:13px; color:#4fb8d8; }
.item-historico { padding:8px; border-radius:6px; margin-bottom:4px; cursor:pointer; }
.item-historico:hover { background:#0a2a3a; }

.principal { flex:1; display:flex; flex-direction:column; }
.topo { padding:16px; border-bottom:1px solid #0a2a3a; font-weight:bold; letter-spacing:1px; }
.mensagens { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.msg { max-width:70%; padding:12px 16px; border-radius:12px; line-height:1.4; }
.msg.usuario { align-self:flex-end; background:#0a2a3a; color:#e6f7ff; }
.msg.jarvis { align-self:flex-start; background:#04141f; border:1px solid #00c8ff33; color:#c8faff; }
.msg img { max-width:100%; border-radius:8px; margin-top:8px; }

.area-input { padding:16px; border-top:1px solid #0a2a3a; display:flex; gap:10px; }
.area-input input { flex:1; padding:14px; border-radius:8px; border:1px solid #00c8ff44; background:#03111c; color:#e6f7ff; font-size:14px; }
.area-input button { padding:14px 20px; border-radius:8px; border:none; background:#00c8ff; color:#001824; font-weight:bold; cursor:pointer; }
.area-input button.imagem { background:#0a2a3a; color:#8fe8ff; border:1px solid #00c8ff44; }
</style>
</head>
<body>

<div class="sidebar">
  <div class="logo-linha">
    <div class="menu-icone"><span></span><span></span><span></span></div>
    <div class="logo-hud"><div class="anel anel1"></div><div class="anel anel2"></div></div>
    <strong>Jarvis</strong>
  </div>
  <button class="novo-chat" onclick="location.reload()">+ Nova conversa</button>
  <div class="historico-lista">Desenvolvido por Samuca</div>
</div>

<div class="principal">
  <div class="topo">JARVIS</div>
  <div class="mensagens" id="mensagens"></div>
  <div class="area-input">
    <input type="text" id="campo" placeholder="Pergunte qualquer coisa..." autocomplete="off">
    <button class="imagem" onclick="gerarImagem()">Gerar imagem</button>
    <button onclick="enviarTexto()">Enviar</button>
  </div>
</div>

<script>
function adicionarMensagem(remetente, conteudoHtml) {
    const div = document.getElementById("mensagens");
    const bolha = document.createElement("div");
    bolha.className = "msg " + remetente;
    bolha.innerHTML = conteudoHtml;
    div.appendChild(bolha);
    div.scrollTop = div.scrollHeight;
}

async function enviarTexto() {
    const campo = document.getElementById("campo");
    const texto = campo.value.trim();
    if (!texto) return;
    adicionarMensagem("usuario", texto);
    campo.value = "";

    const resposta = await fetch("/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mensagem: texto})
    });
    const dados = await resposta.json();
    adicionarMensagem("jarvis", dados.resposta);
}

async function gerarImagem() {
    const campo = document.getElementById("campo");
    const prompt = campo.value.trim();
    if (!prompt) return;
    adicionarMensagem("usuario", "Gerar imagem: " + prompt);
    campo.value = "";

    const resposta = await fetch("/imagem?prompt=" + encodeURIComponent(prompt));
    const dados = await resposta.json();
    adicionarMensagem("jarvis", "Aqui está:<br><img src='" + dados.url + "'>");
}

document.getElementById("campo").addEventListener("keydown", function(e) {
    if (e.key === "Enter") enviarTexto();
});
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return PAGINA


@app.route("/chat", methods=["POST"])
def chat():
    if not _cliente:
        return jsonify({"resposta": "Chave da IA nao configurada no servidor."})

    dados = request.get_json()
    mensagem = dados.get("mensagem", "").strip()
    if not mensagem:
        return jsonify({"resposta": "Nao recebi nenhuma mensagem."})

    historico_sessao = session.get("historico", [])
    historico_sessao.append({"role": "user", "content": mensagem})

    resposta = _cliente.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": SISTEMA}] + historico_sessao[-12:],
        max_tokens=500,
    )
    texto_resposta = resposta.choices[0].message.content
    historico_sessao.append({"role": "assistant", "content": texto_resposta})
    session["historico"] = historico_sessao[-20:]

    return jsonify({"resposta": texto_resposta})


@app.route("/imagem")
def imagem():
    prompt = request.args.get("prompt", "").strip()
    if not prompt:
        return jsonify({"url": ""})
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
    return jsonify({"url": url})


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)
