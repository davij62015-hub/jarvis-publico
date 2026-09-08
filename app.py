"""
=====================================================================
 NOVO GG
=====================================================================
Um unico aplicativo, em tela cheia, parecido com o Discord: amigos
(adicionar por apelido ou por ID numerico), servidores, categorias,
canais de texto e voz, cargos, apelidos por servidor, fixar mensagens,
editar/apagar mensagens, reacoes, emojis customizados do servidor,
indicador de "digitando...", mensagens nao lidas, chamadas de voz e
video (nos canais de voz e tambem nas conversas diretas), descoberta
de servidores publicos e um painel de administrador.

Nao existe nada pago aqui: os recursos que em outros apps seriam
"assinatura" (banner de perfil, decoracao de avatar, mais espaco pra
emoji customizado no servidor, selo de verificado, "impulso" do
servidor) sao liberados manualmente pelo dono do site (administrador),
sem nenhum sistema de cobranca.
=====================================================================
"""
import os
import io
import re
import json
import uuid
import secrets
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# =====================================================================
# Configuracao geral
# =====================================================================

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "troque-esta-chave-em-producao-new-gg-ai")

CAMINHO_BD = os.environ.get("CAMINHO_BD", "newggai.db")
PASTA_UPLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
os.makedirs(PASTA_UPLOADS, exist_ok=True)

AVATAR_PADRAO = "https://api.dicebear.com/7.x/identicon/svg?seed="
NOME_APP = "NOVO GG"
# Dono/admin permanente do site - independe de quem criou a conta primeiro.
# Se o seu apelido de login for outro, troque so essa linha.
CONTA_DONO = "SAMUCA"

# Login com Google (opcional). Sem essa variavel configurada, o botao de
# Google simplesmente nao aparece na tela de login e continua so
# apelido+senha. Crie um Client ID em https://console.cloud.google.com/apis/credentials
# (tipo "Aplicativo da Web", com a URL do site em "Origens JavaScript autorizadas").
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

MINUTOS_CONSIDERADO_ONLINE = 3
SEGUNDOS_DIGITANDO_VALE = 6
EMOJIS_SLOTS_NORMAL = 5
EMOJIS_SLOTS_IMPULSIONADO = 25

# Emojis de reacao (unicode simples, sem nada customizado/protegido)
EMOJIS_REACAO = ["👍", "👎", "😂", "❤️", "😮", "😢", "🔥", "🎉", "👏", "😡"]

# Servidores STUN publicos (gratuitos) para as chamadas de voz/video.
# Se quiser mais confiabilidade em redes fechadas, configure um TURN
# atraves das variaveis de ambiente abaixo.
TURN_URL = os.environ.get("TURN_URL", "")
TURN_USUARIO = os.environ.get("TURN_USUARIO", "")
TURN_SENHA = os.environ.get("TURN_SENHA", "")
_ICE_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
    {"urls": "stun:stun.cloudflare.com:3478"},
]
if TURN_URL:
    _entrada_turn = {"urls": TURN_URL}
    if TURN_USUARIO:
        _entrada_turn["username"] = TURN_USUARIO
    if TURN_SENHA:
        _entrada_turn["credential"] = TURN_SENHA
    _ICE_SERVERS.append(_entrada_turn)
ICE_SERVERS_JSON = json.dumps(_ICE_SERVERS)


@app.after_request
def _adicionar_cabecalhos_seguranca(resposta):
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "SAMEORIGIN"
    resposta.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resposta


@app.route("/static/uploads/")
def _bloquear_listagem_uploads():
    return "Acesso negado.", 403


# =====================================================================
# Banco de dados
# =====================================================================

def obter_bd():
    conexao = sqlite3.connect(CAMINHO_BD)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def _adicionar_coluna_se_faltar(conexao, tabela, coluna, definicao):
    try:
        conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")
    except sqlite3.OperationalError:
        pass


def iniciar_bd():
    conexao = obter_bd()

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            usuario TEXT PRIMARY KEY,
            senha_hash TEXT NOT NULL,
            id_publico INTEGER UNIQUE,
            avatar TEXT,
            banner TEXT,
            bio TEXT,
            status_texto TEXT,
            eh_admin INTEGER DEFAULT 0,
            premium INTEGER DEFAULT 0,
            banido INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL,
            ultima_atividade TEXT
        )
    """)
    for coluna, definicao in [
        ("banner", "TEXT"), ("bio", "TEXT"), ("eh_admin", "INTEGER DEFAULT 0"),
        ("premium", "INTEGER DEFAULT 0"), ("banido", "INTEGER DEFAULT 0"), ("email", "TEXT"),
    ]:
        _adicionar_coluna_se_faltar(conexao, "usuarios", coluna, definicao)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS amizades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            solicitante TEXT NOT NULL,
            destinatario TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            criado_em TEXT NOT NULL,
            atualizado_em TEXT
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS bloqueios (
            usuario TEXT NOT NULL,
            bloqueado TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            PRIMARY KEY (usuario, bloqueado)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS dm_mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversa TEXT NOT NULL,
            remetente TEXT NOT NULL,
            destinatario TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'texto',
            conteudo TEXT NOT NULL,
            editado_em TEXT,
            criado_em TEXT NOT NULL
        )
    """)
    _adicionar_coluna_se_faltar(conexao, "dm_mensagens", "editado_em", "TEXT")

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS servidores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            icone TEXT,
            banner TEXT,
            descricao TEXT,
            cor_faixa TEXT DEFAULT '#5865f2',
            dono TEXT NOT NULL,
            codigo_convite TEXT UNIQUE NOT NULL,
            verificado INTEGER DEFAULT 0,
            impulsionado INTEGER DEFAULT 0,
            publico INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL
        )
    """)
    for coluna, definicao in [
        ("banner", "TEXT"), ("descricao", "TEXT"), ("cor_faixa", "TEXT DEFAULT '#5865f2'"),
        ("verificado", "INTEGER DEFAULT 0"), ("impulsionado", "INTEGER DEFAULT 0"),
        ("publico", "INTEGER DEFAULT 0"),
    ]:
        _adicionar_coluna_se_faltar(conexao, "servidores", coluna, definicao)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS servidor_membros (
            servidor_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            entrou_em TEXT NOT NULL,
            PRIMARY KEY (servidor_id, usuario)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS servidor_banidos (
            servidor_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            PRIMARY KEY (servidor_id, usuario)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servidor_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            ordem INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS canais (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servidor_id INTEGER NOT NULL,
            categoria_id INTEGER,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'texto',
            topico TEXT,
            slowmode INTEGER DEFAULT 0,
            ordem INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL
        )
    """)
    for coluna, definicao in [
        ("categoria_id", "INTEGER"), ("topico", "TEXT"), ("slowmode", "INTEGER DEFAULT 0"),
    ]:
        _adicionar_coluna_se_faltar(conexao, "canais", coluna, definicao)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS canal_mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal_id INTEGER NOT NULL,
            remetente TEXT NOT NULL,
            conteudo TEXT NOT NULL,
            editado_em TEXT,
            fixada INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL
        )
    """)
    for coluna, definicao in [("editado_em", "TEXT"), ("fixada", "INTEGER DEFAULT 0")]:
        _adicionar_coluna_se_faltar(conexao, "canal_mensagens", coluna, definicao)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS cargos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servidor_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            cor TEXT NOT NULL DEFAULT '#99aab5',
            ordem INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS membro_cargos (
            servidor_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            cargo_id INTEGER NOT NULL,
            PRIMARY KEY (servidor_id, usuario, cargo_id)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS apelidos_servidor (
            servidor_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            apelido TEXT,
            PRIMARY KEY (servidor_id, usuario)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS emojis_customizados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servidor_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            url TEXT NOT NULL,
            criado_em TEXT NOT NULL
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS reacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            mensagem_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            emoji TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            UNIQUE(tipo, mensagem_id, usuario, emoji)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS digitando (
            tipo TEXT NOT NULL,
            alvo TEXT NOT NULL,
            usuario TEXT NOT NULL,
            atualizado_em TEXT NOT NULL,
            PRIMARY KEY (tipo, alvo, usuario)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS leituras (
            usuario TEXT NOT NULL,
            tipo TEXT NOT NULL,
            alvo TEXT NOT NULL,
            ultima_msg_id INTEGER DEFAULT 0,
            PRIMARY KEY (usuario, tipo, alvo)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS voz_presenca (
            canal_id INTEGER NOT NULL,
            usuario TEXT NOT NULL,
            entrou_em TEXT NOT NULL,
            PRIMARY KEY (canal_id, usuario)
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS voz_sinal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal_id INTEGER NOT NULL,
            de_usuario TEXT NOT NULL,
            para_usuario TEXT NOT NULL,
            tipo TEXT NOT NULL,
            dados TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            consumido INTEGER DEFAULT 0
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS chamadas_dm (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quem_liga TEXT NOT NULL,
            quem_recebe TEXT NOT NULL,
            oferta TEXT,
            resposta TEXT,
            candidatos_liga TEXT DEFAULT '[]',
            candidatos_recebe TEXT DEFAULT '[]',
            com_video INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'chamando',
            criado_em TEXT NOT NULL
        )
    """)

    conexao.commit()

    sem_id = conexao.execute("SELECT usuario FROM usuarios WHERE id_publico IS NULL").fetchall()
    for linha in sem_id:
        conexao.execute(
            "UPDATE usuarios SET id_publico = ? WHERE usuario = ?",
            (gerar_id_publico(conexao), linha["usuario"]),
        )
    conexao.commit()

    # Garante que sempre exista pelo menos um administrador: se ninguem
    # for admin ainda, a conta mais antiga vira administradora.
    tem_admin = conexao.execute("SELECT 1 FROM usuarios WHERE eh_admin = 1").fetchone()
    if not tem_admin:
        mais_antigo = conexao.execute("SELECT usuario FROM usuarios ORDER BY criado_em ASC LIMIT 1").fetchone()
        if mais_antigo:
            conexao.execute("UPDATE usuarios SET eh_admin = 1 WHERE usuario = ?", (mais_antigo["usuario"],))
            conexao.commit()

    # A conta do dono (CONTA_DONO) e sempre admin e sempre fica com o ID 1,
    # nao importa quando foi criada nem se alguem mais era admin antes.
    conta_dono = conexao.execute("SELECT usuario, id_publico FROM usuarios WHERE usuario = ? COLLATE NOCASE", (CONTA_DONO,)).fetchone()
    if conta_dono:
        conexao.execute("UPDATE usuarios SET eh_admin = 1 WHERE usuario = ? COLLATE NOCASE", (CONTA_DONO,))
        if conta_dono["id_publico"] != 1:
            outro_com_id_1 = conexao.execute("SELECT usuario FROM usuarios WHERE id_publico = 1 AND usuario != ? COLLATE NOCASE", (CONTA_DONO,)).fetchone()
            if outro_com_id_1:
                conexao.execute("UPDATE usuarios SET id_publico = ? WHERE usuario = ?", (gerar_id_publico(conexao), outro_com_id_1["usuario"]))
            conexao.execute("UPDATE usuarios SET id_publico = 1 WHERE usuario = ? COLLATE NOCASE", (CONTA_DONO,))
        conexao.commit()

    conexao.close()


def gerar_id_publico(conexao):
    maior = conexao.execute("SELECT MAX(id_publico) as m FROM usuarios").fetchone()["m"]
    return (maior or 0) + 1


# =====================================================================
# Helpers de usuario / autenticacao / permissoes
# =====================================================================

def usuario_logado():
    return session.get("usuario")


def exigir_login():
    """Retorna None se puder seguir, ou uma resposta de erro caso contrario
    (sessao invalida ou conta banida)."""
    nome = usuario_logado()
    if not nome:
        return jsonify({"ok": False, "erro": "Sessao expirada, entre novamente."}), 401
    linha = buscar_usuario(nome)
    if not linha:
        session.pop("usuario", None)
        return jsonify({"ok": False, "erro": "Sessao invalida, entre novamente."}), 401
    if linha["banido"]:
        session.pop("usuario", None)
        return jsonify({"ok": False, "erro": "Sua conta foi banida.", "banido": True}), 403
    return None


def buscar_usuario(nome):
    if not nome:
        return None
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE usuario = ? COLLATE NOCASE", (nome,)).fetchone()
    conexao.close()
    return linha


def buscar_usuario_por_id(id_publico):
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE id_publico = ?", (id_publico,)).fetchone()
    conexao.close()
    return linha


def buscar_usuario_por_nick_ou_id(valor):
    valor = (valor or "").strip()
    if not valor:
        return None
    valor_sem_hash = valor.lstrip("#")
    if valor_sem_hash.isdigit():
        linha = buscar_usuario_por_id(int(valor_sem_hash))
        if linha:
            return linha
    return buscar_usuario(valor)


def avatar_de(usuario_ou_linha):
    if isinstance(usuario_ou_linha, sqlite3.Row):
        nome = usuario_ou_linha["usuario"]
        avatar = usuario_ou_linha["avatar"]
    else:
        linha = buscar_usuario(usuario_ou_linha)
        nome = usuario_ou_linha
        avatar = linha["avatar"] if linha else None
    return avatar if avatar else (AVATAR_PADRAO + nome)


def eh_admin(usuario):
    if usuario and usuario.strip().lower() == CONTA_DONO.lower():
        return True
    linha = buscar_usuario(usuario)
    return bool(linha and linha["eh_admin"])


def eh_premium(usuario):
    linha = buscar_usuario(usuario)
    return bool(linha and (linha["premium"] or linha["eh_admin"]))


def marcar_atividade(usuario):
    if not usuario:
        return
    conexao = obter_bd()
    conexao.execute(
        "UPDATE usuarios SET ultima_atividade = ? WHERE usuario = ? COLLATE NOCASE",
        (datetime.now().isoformat(), usuario),
    )
    conexao.commit()
    conexao.close()


def esta_online(ultima_atividade_iso):
    if not ultima_atividade_iso:
        return False
    try:
        momento = datetime.fromisoformat(ultima_atividade_iso)
    except ValueError:
        return False
    return datetime.now() - momento <= timedelta(minutes=MINUTOS_CONSIDERADO_ONLINE)


def usuarios_sao_bloqueados(usuario_a, usuario_b):
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT 1 FROM bloqueios WHERE (usuario = ? COLLATE NOCASE AND bloqueado = ? COLLATE NOCASE) "
        "OR (usuario = ? COLLATE NOCASE AND bloqueado = ? COLLATE NOCASE)",
        (usuario_a, usuario_b, usuario_b, usuario_a),
    ).fetchone()
    conexao.close()
    return linha is not None


def id_conversa_dm(usuario_a, usuario_b):
    return "|".join(sorted([usuario_a.lower(), usuario_b.lower()]))


def salvar_imagem_enviada(arquivo):
    if not arquivo or not arquivo.filename:
        return None
    extensao = os.path.splitext(arquivo.filename)[1].lower()
    if extensao not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        extensao = ".png"
    nome_seguro = secure_filename(arquivo.filename) or "arquivo"
    nome_unico = f"{uuid.uuid4().hex}_{nome_seguro}"
    caminho = os.path.join(PASTA_UPLOADS, nome_unico)
    arquivo.save(caminho)
    return f"/static/uploads/{nome_unico}"


def gerar_codigo_convite():
    while True:
        codigo = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8]
        conexao = obter_bd()
        existe = conexao.execute("SELECT 1 FROM servidores WHERE codigo_convite = ?", (codigo,)).fetchone()
        conexao.close()
        if not existe:
            return codigo


def eh_membro_do_servidor(servidor_id, usuario):
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT 1 FROM servidor_membros WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE",
        (servidor_id, usuario),
    ).fetchone()
    conexao.close()
    return linha is not None


def eh_dono_do_servidor(servidor_id, usuario):
    conexao = obter_bd()
    linha = conexao.execute("SELECT dono FROM servidores WHERE id = ?", (servidor_id,)).fetchone()
    conexao.close()
    return bool(linha and usuario and linha["dono"].lower() == usuario.lower())


def pode_gerenciar_servidor(servidor_id, usuario):
    """Dono do servidor OU administrador global podem gerenciar tudo."""
    return eh_dono_do_servidor(servidor_id, usuario) or eh_admin(usuario)


def canal_pertence_a_membro(canal_id, usuario):
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT c.servidor_id FROM canais c JOIN servidor_membros m ON m.servidor_id = c.servidor_id "
        "WHERE c.id = ? AND m.usuario = ? COLLATE NOCASE",
        (canal_id, usuario),
    ).fetchone()
    conexao.close()
    return linha["servidor_id"] if linha else None


def apelido_no_servidor(servidor_id, usuario):
    conexao = obter_bd()
    linha = conexao.execute(
        "SELECT apelido FROM apelidos_servidor WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE",
        (servidor_id, usuario),
    ).fetchone()
    conexao.close()
    return linha["apelido"] if linha and linha["apelido"] else None


def nome_exibicao_no_servidor(servidor_id, usuario):
    return apelido_no_servidor(servidor_id, usuario) or usuario


def montar_reacoes(tipo, ids_mensagens, usuario_atual):
    """Retorna um dicionario {mensagem_id: [{"emoji":..., "qtd":..., "reagi": bool}, ...]}."""
    if not ids_mensagens:
        return {}
    conexao = obter_bd()
    marcadores = ",".join("?" * len(ids_mensagens))
    linhas = conexao.execute(
        f"SELECT mensagem_id, emoji, usuario FROM reacoes WHERE tipo = ? AND mensagem_id IN ({marcadores})",
        [tipo] + list(ids_mensagens),
    ).fetchall()
    conexao.close()
    agrupado = {}
    for l in linhas:
        chave = (l["mensagem_id"], l["emoji"])
        if chave not in agrupado:
            agrupado[chave] = {"qtd": 0, "reagi": False}
        agrupado[chave]["qtd"] += 1
        if l["usuario"].lower() == (usuario_atual or "").lower():
            agrupado[chave]["reagi"] = True
    resultado = {}
    for (mensagem_id, emoji), info in agrupado.items():
        resultado.setdefault(mensagem_id, []).append({"emoji": emoji, "qtd": info["qtd"], "reagi": info["reagi"]})
    return resultado


def marcar_lido(usuario, tipo, alvo, ultima_msg_id):
    if not ultima_msg_id:
        return
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO leituras (usuario, tipo, alvo, ultima_msg_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(usuario, tipo, alvo) DO UPDATE SET ultima_msg_id = MAX(ultima_msg_id, excluded.ultima_msg_id)",
        (usuario, tipo, str(alvo), ultima_msg_id),
    )
    conexao.commit()
    conexao.close()


iniciar_bd()


# =====================================================================
# Estilos e HTML compartilhados
# =====================================================================

ESTILO_BASE = """
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
html, body { height: 100%; overflow: hidden; background: #1e1f22; }
body { color: #dbdee1; }
img, video { max-width: 100%; }
button, input, textarea, select { font-family: inherit; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #1a1b1e; border-radius: 4px; }
a { color: inherit; text-decoration: none; }
"""

FONTE_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700&display=swap" rel="stylesheet">'
)


def pagina_html(titulo, corpo, estilos_extra="", scripts_extra=""):
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{titulo}</title>
{FONTE_LINK}
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/svg+xml" href="/static/logo.svg">
<link rel="apple-touch-icon" href="/static/logo.svg">
<meta name="theme-color" content="#1e1f22">
<style>{ESTILO_BASE}
body {{ font-family: 'Rubik', 'Segoe UI', sans-serif; }}
{estilos_extra}
</style>
</head>
<body>
{corpo}
{scripts_extra}
</body>
</html>"""


# =====================================================================
# Pagina de login / cadastro
# =====================================================================

ESTILO_LOGIN = """
body { display:flex; align-items:center; justify-content:center; height:100vh;
       background: radial-gradient(circle at 50% 20%, #2b2d31 0%, #1e1f22 60%); overflow-y:auto; }
.caixa-login { width:100%; max-width:400px; background:#313338; border-radius:12px; padding:32px 28px;
               box-shadow:0 20px 60px #00000066; margin:20px; }
.marca-login { text-align:center; margin-bottom:22px; }
.marca-login .bolha-logo { width:72px; height:72px; border-radius:18px; margin:0 auto 12px;
               box-shadow:0 8px 24px #5865f255; }
.marca-login .bolha-logo img { width:100%; height:100%; display:block; }
.marca-login h1 { font-size:20px; font-weight:700; color:#fff; }
.marca-login p { font-size:13px; color:#949ba4; margin-top:4px; }
.abas-login { display:flex; background:#1e1f22; border-radius:8px; padding:4px; margin-bottom:20px; }
.abas-login button { flex:1; border:none; background:none; color:#949ba4; padding:9px; border-radius:6px; font-size:13px;
                      font-weight:600; cursor:pointer; }
.abas-login button.ativa { background:#404249; color:#fff; }
.campo-login { margin-bottom:16px; }
.campo-login label { display:block; font-size:12px; font-weight:700; color:#b5bac1; text-transform:uppercase;
                      letter-spacing:0.02em; margin-bottom:8px; }
.campo-login input { width:100%; padding:11px 12px; border-radius:4px; border:none; background:#1e1f22; color:#dbdee1;
                      font-size:15px; }
.campo-login input:focus { outline:2px solid #00a8fc; }
.botao-login { width:100%; padding:12px; border-radius:4px; border:none; background:#5865f2; color:#fff;
               font-weight:600; font-size:15px; cursor:pointer; margin-top:6px; }
.botao-login:hover { background:#4752c4; }
.botao-login:disabled { opacity:0.6; cursor:default; }
.erro-login { color:#fa777c; font-size:13px; margin-top:12px; min-height:16px; text-align:center; }
.aviso-id { font-size:12px; color:#949ba4; margin-top:-8px; margin-bottom:16px; }
.divisor-login { display:flex; align-items:center; gap:10px; color:#80848e; font-size:12px; margin:18px 0; }
.divisor-login::before, .divisor-login::after { content:""; flex:1; height:1px; background:#40424980; }
.bloco-google-login { display:flex; justify-content:center; margin-bottom:4px; }
"""

CORPO_LOGIN = """
<div class="caixa-login">
  <div class="marca-login">
    <div class="bolha-logo"><img src="/static/logo.png" alt="NOVO GG"></div>
    <h1>NOVO GG</h1>
    <p>Converse com seus amigos e servidores.</p>
  </div>
  {bloco_google}
  <div class="abas-login">
    <button id="abaEntrar" class="ativa" onclick="mudarAba('entrar')">Entrar</button>
    <button id="abaCriar" onclick="mudarAba('criar')">Criar conta</button>
  </div>

  <form id="formEntrar" onsubmit="return enviarEntrar(event)">
    <div class="campo-login"><label>Apelido</label><input type="text" id="loginUsuario" autocomplete="username" required></div>
    <div class="campo-login"><label>Senha</label><input type="password" id="loginSenha" autocomplete="current-password" required></div>
    <button class="botao-login" type="submit" id="botaoEntrar">Entrar</button>
    <div class="erro-login" id="erroEntrar"></div>
  </form>

  <form id="formCriar" style="display:none;" onsubmit="return enviarCriar(event)">
    <div class="campo-login"><label>Apelido</label><input type="text" id="criarUsuario" maxlength="32" required></div>
    <div class="campo-login"><label>Senha</label><input type="password" id="criarSenha" minlength="4" required></div>
    <div class="aviso-id">Voce recebe um ID numerico unico automaticamente (pode ser usado por outras pessoas para te adicionar). Nao existe nada pago aqui - alguns recursos extras so podem ser liberados pelo administrador.</div>
    <button class="botao-login" type="submit" id="botaoCriar">Criar conta</button>
    <div class="erro-login" id="erroCriar"></div>
  </form>
</div>
<script>
function mudarAba(aba) {
    document.getElementById('abaEntrar').classList.toggle('ativa', aba === 'entrar');
    document.getElementById('abaCriar').classList.toggle('ativa', aba === 'criar');
    document.getElementById('formEntrar').style.display = aba === 'entrar' ? 'block' : 'none';
    document.getElementById('formCriar').style.display = aba === 'criar' ? 'block' : 'none';
}
async function enviarEntrar(ev) {
    ev.preventDefault();
    const botao = document.getElementById('botaoEntrar');
    const erro = document.getElementById('erroEntrar');
    erro.textContent = '';
    botao.disabled = true; botao.textContent = 'Entrando...';
    try {
        const r = await fetch('/entrar', { method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ usuario: document.getElementById('loginUsuario').value.trim(),
                                    senha: document.getElementById('loginSenha').value }) });
        const d = await r.json();
        if (d.ok) { window.location.href = '/app'; }
        else { erro.textContent = d.erro || 'Nao foi possivel entrar.'; }
    } catch (e) { erro.textContent = 'Falha de conexao.'; }
    botao.disabled = false; botao.textContent = 'Entrar';
    return false;
}
async function enviarCriar(ev) {
    ev.preventDefault();
    const botao = document.getElementById('botaoCriar');
    const erro = document.getElementById('erroCriar');
    erro.textContent = '';
    botao.disabled = true; botao.textContent = 'Criando...';
    try {
        const r = await fetch('/registrar', { method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ usuario: document.getElementById('criarUsuario').value.trim(),
                                    senha: document.getElementById('criarSenha').value }) });
        const d = await r.json();
        if (d.ok) { window.location.href = '/app'; }
        else { erro.textContent = d.erro || 'Nao foi possivel criar a conta.'; }
    } catch (e) { erro.textContent = 'Falha de conexao.'; }
    botao.disabled = false; botao.textContent = 'Criar conta';
    return false;
}
function aoLoginGoogle(resposta) {
    const erro = document.getElementById('erroEntrar');
    erro.textContent = '';
    fetch('/auth/google', { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ credential: resposta.credential }) })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { window.location.href = '/app'; }
        else { erro.textContent = d.erro || 'Nao foi possivel entrar com o Google.'; }
    })
    .catch(() => { erro.textContent = 'Falha ao conectar com o Google.'; });
}
</script>
"""

PAGINA_BANIDO = """
<div style="height:100vh; display:flex; align-items:center; justify-content:center; background:radial-gradient(circle at 50% 20%, #2b2d31 0%, #1e1f22 60%); padding:20px;">
  <div style="width:100%; max-width:380px; background:#313338; border-radius:12px; padding:36px 28px; text-align:center;
              box-shadow:0 20px 60px #00000066; animation:apareceBanido .35s ease;">
    <div style="width:76px; height:76px; border-radius:50%; background:#da373c22; border:2px solid #da373c55;
                display:flex; align-items:center; justify-content:center; font-size:34px; margin:0 auto 20px;">&#128683;</div>
    <h2 style="color:#fff; font-size:20px; font-weight:700; margin-bottom:10px;">Sua conta foi banida</h2>
    <p style="color:#949ba4; font-size:14px; line-height:1.5; margin-bottom:26px;">
      O administrador do NOVO GG restringiu o acesso desta conta. Se voce acha que foi um engano, fale com quem administra o app.
    </p>
    <a href="/sair" style="display:block; width:100%; padding:12px; border-radius:4px; background:#5865f2; color:#fff;
              font-weight:600; font-size:14px; box-sizing:border-box; transition:background .15s ease;"
       onmouseover="this.style.background='#4752c4'" onmouseout="this.style.background='#5865f2'">Sair da conta</a>
  </div>
</div>
<style>@keyframes apareceBanido { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }</style>
"""


@app.route("/")
def raiz():
    if usuario_logado():
        linha = buscar_usuario(usuario_logado())
        if linha and linha["banido"]:
            return pagina_html(NOME_APP, PAGINA_BANIDO)
        return redirect(url_for("pagina_app"))
    if GOOGLE_CLIENT_ID:
        bloco_google = f"""
        <script src="https://accounts.google.com/gsi/client" async defer></script>
        <div id="g_id_onload" data-client_id="{GOOGLE_CLIENT_ID}" data-callback="aoLoginGoogle" data-auto_prompt="false"></div>
        <div class="bloco-google-login">
          <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black"
               data-text="continue_with" data-size="large" data-logo_alignment="left" data-width="336"></div>
        </div>
        <div class="divisor-login">ou continue com apelido e senha</div>
        """
    else:
        bloco_google = ""
    corpo = CORPO_LOGIN.replace("{bloco_google}", bloco_google)
    return pagina_html(NOME_APP, corpo, ESTILO_LOGIN)


@app.route("/auth/google", methods=["POST"])
def auth_google():
    """Verifica o token do Google Identity Services e loga (ou cria a conta)."""
    if not GOOGLE_CLIENT_ID:
        return jsonify({"ok": False, "erro": "Login com Google nao esta configurado neste servidor."}), 400
    dados = request.get_json() or {}
    token = (dados.get("credential") or "").strip()
    if not token:
        return jsonify({"ok": False, "erro": "Token ausente."}), 400
    try:
        with urllib.request.urlopen(
            "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(token), timeout=8
        ) as resposta_http:
            info = json.loads(resposta_http.read().decode())
    except Exception:
        return jsonify({"ok": False, "erro": "Nao foi possivel validar o login do Google."}), 400
    if info.get("aud") != GOOGLE_CLIENT_ID:
        return jsonify({"ok": False, "erro": "Token nao pertence a este site."}), 400
    email = (info.get("email") or "").strip().lower()
    if not email or str(info.get("email_verified")).lower() != "true":
        return jsonify({"ok": False, "erro": "Email do Google nao verificado."}), 400
    nome_google = (info.get("name") or email.split("@")[0]).strip()

    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if linha:
        conexao.close()
        if linha["banido"]:
            return jsonify({"ok": False, "erro": "Sua conta foi banida."})
        session["usuario"] = linha["usuario"]
        marcar_atividade(linha["usuario"])
        return jsonify({"ok": True})

    usuario_base = re.sub(r"[#|/\\\\]", "", nome_google).strip() or email.split("@")[0]
    usuario_base = usuario_base[:32] or "usuario"
    usuario_final = usuario_base
    contador = 1
    while conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario_final,)).fetchone():
        contador += 1
        usuario_final = f"{usuario_base}{contador}"[:32]
    id_publico = gerar_id_publico(conexao)
    eh_primeira_conta = conexao.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"] == 0
    conexao.execute(
        "INSERT INTO usuarios (usuario, senha_hash, id_publico, eh_admin, email, criado_em, ultima_atividade) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (usuario_final, generate_password_hash(uuid.uuid4().hex), id_publico, 1 if eh_primeira_conta else 0,
         email, datetime.now().isoformat(), datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    session["usuario"] = usuario_final
    return jsonify({"ok": True})


@app.route("/registrar", methods=["POST"])
def registrar():
    dados = request.get_json() or {}
    usuario = (dados.get("usuario") or "").strip()
    senha = dados.get("senha") or ""
    if not usuario or len(usuario) < 2:
        return jsonify({"ok": False, "erro": "Escolha um apelido com pelo menos 2 caracteres."})
    if any(c in usuario for c in "#|/\\"):
        return jsonify({"ok": False, "erro": "O apelido nao pode conter #, / ou \\."})
    if len(senha) < 4:
        return jsonify({"ok": False, "erro": "A senha precisa ter pelo menos 4 caracteres."})
    conexao = obter_bd()
    existe = conexao.execute("SELECT 1 FROM usuarios WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchone()
    if existe:
        conexao.close()
        return jsonify({"ok": False, "erro": "Esse apelido ja esta em uso."})
    id_publico = gerar_id_publico(conexao)
    eh_primeira_conta = conexao.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"] == 0
    conexao.execute(
        "INSERT INTO usuarios (usuario, senha_hash, id_publico, eh_admin, criado_em, ultima_atividade) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (usuario, generate_password_hash(senha), id_publico, 1 if eh_primeira_conta else 0,
         datetime.now().isoformat(), datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    session["usuario"] = usuario
    return jsonify({"ok": True})


@app.route("/entrar", methods=["POST"])
def entrar():
    dados = request.get_json() or {}
    usuario = (dados.get("usuario") or "").strip()
    senha = dados.get("senha") or ""
    linha = buscar_usuario(usuario)
    if not linha or not check_password_hash(linha["senha_hash"], senha):
        return jsonify({"ok": False, "erro": "Apelido ou senha incorretos."})
    if linha["banido"]:
        return jsonify({"ok": False, "erro": "Sua conta foi banida."})
    session["usuario"] = linha["usuario"]
    marcar_atividade(linha["usuario"])
    return jsonify({"ok": True})


@app.route("/sair")
def sair():
    session.pop("usuario", None)
    return redirect(url_for("raiz"))


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": NOME_APP, "short_name": NOME_APP, "start_url": "/app", "display": "standalone",
        "background_color": "#1e1f22", "theme_color": "#1e1f22",
        "icons": [
            {"src": "/static/logo.svg", "sizes": "192x192", "type": "image/svg+xml", "purpose": "any"},
            {"src": "/static/logo.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any"},
            {"src": "/static/logo.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "maskable"},
        ],
    })


# =====================================================================
# Pagina principal (SPA em tela cheia, estilo Discord)
# =====================================================================

ESTILO_APP = """
html, body { height:100%; overflow:hidden; }
#appShell { display:flex; height:100vh; width:100vw; }

#railServidores { width:72px; flex-shrink:0; background:#1e1f22; display:flex; flex-direction:column;
                   align-items:center; padding:12px 0; gap:8px; overflow-y:auto; }
.rail-item { width:48px; height:48px; border-radius:50%; background:#313338; color:#fff; display:flex;
             align-items:center; justify-content:center; cursor:pointer; font-weight:700; font-size:16px;
             overflow:hidden; transition:border-radius .15s ease, background .15s ease; position:relative; flex-shrink:0; }
.rail-item:hover, .rail-item.ativo { border-radius:16px; background:#5865f2; }
.rail-item img { width:100%; height:100%; object-fit:cover; }
.rail-item .pastilha { position:absolute; left:-12px; top:50%; transform:translateY(-50%); width:4px; height:8px;
             background:#fff; border-radius:0 4px 4px 0; opacity:0; transition:.15s ease; }
.rail-item:hover .pastilha, .rail-item.ativo .pastilha { opacity:1; height:20px; }
.rail-item .ponto-nao-lido { position:absolute; left:-4px; bottom:-2px; width:10px; height:10px; border-radius:50%; background:#fff; border:3px solid #1e1f22; }
.rail-item.impulsionado { box-shadow:0 0 0 2px #f47fff; }
.rail-separador { width:32px; height:2px; background:#35373c; border-radius:2px; margin:2px 0; flex-shrink:0; }
.rail-item.adicionar { background:#313338; color:#3ba55c; font-size:22px; }
.rail-item.adicionar:hover { background:#3ba55c; color:#fff; }
.rail-item.dm-icone { font-size:20px; }
.rail-item.admin-icone { background:#313338; color:#fee75c; font-size:18px; }
.rail-item.admin-icone:hover { background:#fee75c; color:#111; }

#segundaColuna { width:240px; flex-shrink:0; background:#2b2d31; display:flex; flex-direction:column; min-width:0; }
.topo-coluna { height:48px; flex-shrink:0; display:flex; align-items:center; padding:0 16px; font-weight:700;
               color:#fff; font-size:15px; box-shadow:0 1px 0 #1f2023; gap:8px; }
.busca-dm { margin:10px 8px; }
.busca-dm input { width:100%; padding:7px 8px; border-radius:4px; border:none; background:#1e1f22; color:#dbdee1; font-size:13px; }
.abas-social { display:flex; flex-direction:column; padding:4px 8px 8px; gap:2px; }
.item-social { padding:8px 10px; border-radius:4px; color:#949ba4; font-size:14.5px; font-weight:500; cursor:pointer; position:relative; }
.item-social:hover { background:#35373c; color:#dbdee1; }
.item-social.ativo { background:#404249; color:#fff; }
.item-social.destaque-add { color:#3ba55c; }
.badge-contagem { float:right; background:#da373c; color:#fff; font-size:11px; font-weight:700; border-radius:8px; padding:0 6px; }
.lista-lateral { flex:1; overflow-y:auto; padding:0 8px 8px; }
.linha-secao { padding:14px 8px 4px; font-size:11px; font-weight:700; color:#949ba4; text-transform:uppercase; letter-spacing:.02em; }
.item-amigo { display:flex; align-items:center; gap:10px; padding:6px 8px; border-radius:6px; cursor:pointer; }
.item-amigo:hover { background:#35373c; }
.item-amigo.ativo { background:#404249; }
.item-amigo .ponto-nao-lido-dm { width:8px; height:8px; border-radius:50%; background:#fff; margin-left:auto; flex-shrink:0; }
.avatar-com-status { position:relative; width:32px; height:32px; flex-shrink:0; }
.avatar-com-status img { width:32px; height:32px; border-radius:50%; object-fit:cover; background:#1e1f22; }
.avatar-com-status.premium img { box-shadow:0 0 0 2px #f47fff; }
.bolinha-status { position:absolute; right:-2px; bottom:-2px; width:12px; height:12px; border-radius:50%;
                   background:#80848e; border:3px solid #2b2d31; box-sizing:content-box; }
.bolinha-status.online { background:#3ba55c; }
.info-amigo { flex:1; min-width:0; }
.info-amigo .nome-amigo { font-size:14px; color:#dbdee1; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.info-amigo .sub-amigo { font-size:11.5px; color:#949ba4; }
.acoes-item-amigo { display:flex; gap:4px; }
.botao-mini-circulo { width:28px; height:28px; border-radius:50%; background:#1e1f22; border:none; color:#dbdee1;
             display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:13px; }
.botao-mini-circulo:hover { background:#3ba55c; color:#fff; }
.botao-mini-circulo.recusar:hover { background:#da373c; }
.vazio-lista-lateral { color:#80848e; font-size:13px; text-align:center; padding:30px 16px; }
.selo-verificado-nome { color:#00a8fc; margin-left:4px; }
.selo-impulso-nome { color:#f47fff; margin-left:4px; }
.selo-admin-nome { color:#fee75c; margin-left:4px; }

.rodape-usuario { height:52px; flex-shrink:0; background:#232428; display:flex; align-items:center; padding:0 8px; gap:8px; }
.rodape-usuario img { width:32px; height:32px; border-radius:50%; object-fit:cover; cursor:pointer; }
.rodape-usuario .nome-rodape { font-size:13px; font-weight:600; color:#fff; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rodape-usuario .id-rodape { font-size:11px; color:#949ba4; }
.rodape-usuario .info-rodape { flex:1; min-width:0; cursor:pointer; }
.rodape-usuario .botao-sair-rodape { background:none; border:none; color:#b5bac1; font-size:16px; cursor:pointer; padding:6px; border-radius:4px; }
.rodape-usuario .botao-sair-rodape:hover { background:#35373c; color:#fff; }

#areaPrincipal { flex:1; display:flex; flex-direction:column; min-width:0; background:#313338; }
.topo-principal { height:48px; flex-shrink:0; display:flex; align-items:center; padding:0 16px; box-shadow:0 1px 0 #26272b;
                   gap:10px; color:#fff; font-weight:700; font-size:15px; }
.topo-principal .topico-canal-topo { font-weight:400; font-size:13px; color:#949ba4; margin-left:8px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.topo-principal img.avatar-topo { width:24px; height:24px; border-radius:50%; object-fit:cover; }
.topo-principal .acoes-topo { margin-left:auto; display:flex; gap:14px; align-items:center; flex-shrink:0; }
.topo-principal .acoes-topo span { cursor:pointer; color:#b5bac1; font-size:18px; }
.topo-principal .acoes-topo span:hover { color:#fff; }

.corpo-principal { flex:1; overflow-y:auto; display:flex; flex-direction:column; min-height:0; }
.tela-boas-vindas { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; color:#80848e;
                     text-align:center; padding:20px; gap:8px; }
.tela-boas-vindas .bolha-grande { width:80px; height:80px; border-radius:50%; background:#404249; display:flex;
                     align-items:center; justify-content:center; font-size:34px; margin-bottom:6px; }

.painel-amigos-central { flex:1; padding:16px 24px; overflow-y:auto; }
.contador-aba { color:#fff; font-size:15px; font-weight:700; margin-bottom:14px; }
.linha-pendente { display:flex; align-items:center; gap:12px; padding:10px 4px; border-bottom:1px solid #3a3c41; }
.linha-pendente .info-amigo { flex:1; }
.linha-pendente .sub-amigo { color:#949ba4; font-size:12px; }

.lista-mensagens { flex:1; overflow-y:auto; padding:12px 16px; display:flex; flex-direction:column; gap:2px; }
.grupo-mensagem { display:flex; gap:14px; padding:6px 4px; border-radius:6px; position:relative; }
.grupo-mensagem:hover { background:#2e3035; }
.grupo-mensagem:hover .acoes-mensagem { display:flex; }
.grupo-mensagem img.avatar-msg { width:40px; height:40px; border-radius:50%; object-fit:cover; flex-shrink:0; margin-top:2px; cursor:pointer; }
.conteudo-msg-grupo { flex:1; min-width:0; }
.cabecalho-msg { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
.cabecalho-msg .autor-msg { font-weight:600; color:#f2f3f5; font-size:15px; cursor:pointer; }
.cabecalho-msg .hora-msg { font-size:11px; color:#949ba4; }
.cabecalho-msg .editado-msg { font-size:10px; color:#80848e; }
.cabecalho-msg .pin-msg-tag { font-size:10px; color:#f0b232; }
.texto-msg { font-size:15px; color:#dbdee1; white-space:pre-wrap; word-break:break-word; line-height:1.4; }
.texto-msg img.emoji-custom { height:22px; vertical-align:middle; }
.vazio-mensagens { color:#80848e; text-align:center; padding:40px 16px; font-size:13px; }
.acoes-mensagem { display:none; position:absolute; top:-14px; right:8px; background:#313338; border:1px solid #232428;
                   border-radius:6px; padding:2px; gap:2px; }
.acoes-mensagem button { background:none; border:none; color:#b5bac1; font-size:14px; padding:5px 7px; cursor:pointer; border-radius:4px; }
.acoes-mensagem button:hover { background:#3a3c41; color:#fff; }
.faixa-reacoes { display:flex; gap:6px; flex-wrap:wrap; margin-top:6px; }
.pilula-reacao { background:#2b2d31; border:1px solid #3a3c41; border-radius:12px; padding:2px 8px; font-size:12.5px;
                  cursor:pointer; display:flex; align-items:center; gap:4px; color:#dbdee1; }
.pilula-reacao.minha { background:#3c4270; border-color:#5865f2; }
.pilula-reacao:hover { border-color:#5865f2; }
.seletor-reacao-rapida { position:absolute; top:-40px; right:36px; background:#313338; border-radius:16px; padding:4px 6px;
                   display:none; gap:2px; border:1px solid #232428; }
.seletor-reacao-rapida.aberto { display:flex; }
.seletor-reacao-rapida span { cursor:pointer; font-size:18px; padding:2px; border-radius:4px; }
.seletor-reacao-rapida span:hover { background:#3a3c41; }
.editando-mensagem-caixa { display:flex; gap:6px; margin-top:4px; }
.editando-mensagem-caixa input { flex:1; background:#1e1f22; border:1px solid #5865f2; border-radius:4px; color:#dbdee1; padding:6px 8px; font-size:14px; }
.editando-mensagem-caixa button { background:none; border:none; color:#00a8fc; cursor:pointer; font-size:12px; }

.indicador-digitando { padding:0 16px 4px; font-size:12px; color:#949ba4; font-style:italic; min-height:16px; }

.area-input-mensagem { padding:0 16px 20px; flex-shrink:0; }
.caixa-input-msg { background:#383a40; border-radius:8px; display:flex; align-items:center; padding:4px 12px; gap:10px; }
.caixa-input-msg input { flex:1; background:none; border:none; color:#dbdee1; padding:11px 0; font-size:15px; }
.caixa-input-msg input:focus { outline:none; }
.caixa-input-msg button { background:none; border:none; color:#b5bac1; cursor:pointer; font-size:18px; padding:6px; }
.caixa-input-msg button:hover { color:#fff; }

#painelMembros { width:236px; flex-shrink:0; background:#2b2d31; overflow-y:auto; padding:16px 10px; display:none; }
#painelMembros.aberto { display:block; }
.titulo-membros { font-size:12px; font-weight:700; color:#949ba4; text-transform:uppercase; padding:8px 6px; }
.linha-membro-servidor { display:flex; align-items:center; gap:10px; padding:6px; border-radius:6px; cursor:pointer; }
.linha-membro-servidor:hover { background:#35373c; }
.linha-membro-servidor .nome-membro-serv { font-size:14px; color:#dbdee1; }
.badge-cargo-membro { display:inline-block; font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px; margin-left:6px; color:#000; }

.painel-voz-central { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:24px; padding:20px; }
.grade-voz-participantes { display:flex; gap:18px; flex-wrap:wrap; justify-content:center; }
.card-voz-participante { display:flex; flex-direction:column; align-items:center; gap:8px; }
.card-voz-participante img.avatar-voz { width:76px; height:76px; border-radius:50%; object-fit:cover; border:3px solid transparent; }
.card-voz-participante.falando img.avatar-voz { border-color:#3ba55c; }
.card-voz-participante video { width:180px; height:120px; border-radius:8px; object-fit:cover; background:#000; }
.botoes-controle-voz { display:flex; gap:16px; }
.botoes-controle-voz button { width:54px; height:54px; border-radius:50%; border:none; font-size:20px; cursor:pointer;
                    background:#404249; color:#fff; }
.botoes-controle-voz button.entrar { background:#3ba55c; }
.botoes-controle-voz button.sair { background:#da373c; }
.botoes-controle-voz button.ativado { background:#5865f2; }

.grupo-canais-titulo { display:flex; align-items:center; padding:16px 4px 4px; font-size:11px; font-weight:700;
                        color:#949ba4; text-transform:uppercase; letter-spacing:.02em; cursor:pointer; }
.grupo-canais-titulo .add-canal-btn { margin-left:auto; cursor:pointer; font-size:16px; color:#949ba4; }
.grupo-canais-titulo .add-canal-btn:hover { color:#fff; }
.item-canal-servidor { display:flex; align-items:center; gap:6px; padding:7px 8px; border-radius:4px; color:#949ba4;
                        font-size:15px; cursor:pointer; margin-bottom:1px; }
.item-canal-servidor:hover { background:#35373c; color:#dbdee1; }
.item-canal-servidor.ativo { background:#404249; color:#fff; }
.item-canal-servidor .contagem-voz { margin-left:auto; font-size:11px; color:#3ba55c; }
.item-canal-servidor .engrenagem-canal { margin-left:auto; opacity:0; font-size:13px; }
.item-canal-servidor:hover .engrenagem-canal { opacity:1; }
.item-canal-servidor .ponto-nao-lido-canal { width:6px; height:6px; border-radius:50%; background:#fff; margin-left:auto; }

.cabecalho-servidor-topo { height:48px; flex-shrink:0; display:flex; align-items:center; padding:0 16px; font-weight:700;
                            color:#fff; box-shadow:0 1px 0 #1f2023; cursor:pointer; gap:6px; position:relative; }
.cabecalho-servidor-topo .seta-servidor { margin-left:auto; color:#949ba4; }

.menu-flutuante-servidor { position:absolute; top:46px; left:8px; right:8px; background:#111214; border-radius:8px;
                            padding:6px; z-index:60; box-shadow:0 8px 24px #000000aa; display:none; max-height:70vh; overflow-y:auto; }
.menu-flutuante-servidor.aberto { display:block; }
.item-menu-flutuante { padding:9px 10px; border-radius:6px; font-size:13.5px; color:#dbdee1; cursor:pointer; }
.item-menu-flutuante:hover { background:#4752c4; color:#fff; }
.item-menu-flutuante.perigo { color:#da373c; }
.item-menu-flutuante.perigo:hover { background:#da373c; color:#fff; }

.fundo-modal { display:none; position:fixed; inset:0; background:#00000088; z-index:200; align-items:center; justify-content:center;
               padding:16px; }
.fundo-modal.aberto { display:flex; }
.caixa-modal { background:#313338; border-radius:8px; width:100%; max-width:460px; max-height:88vh; overflow-y:auto; }
.caixa-modal.grande { max-width:640px; }
.caixa-modal .topo-modal { padding:16px 16px 0; }
.caixa-modal .topo-modal h2 { color:#fff; font-size:20px; text-align:center; }
.caixa-modal .topo-modal p { color:#949ba4; font-size:13px; text-align:center; margin-top:6px; }
.caixa-modal .corpo-modal { padding:16px 16px 20px; }
.caixa-modal label { display:block; font-size:12px; font-weight:700; color:#b5bac1; text-transform:uppercase; margin-bottom:8px; }
.caixa-modal input[type=text], .caixa-modal input[type=number], .caixa-modal input[type=file], .caixa-modal textarea, .caixa-modal select {
                width:100%; padding:10px 12px; border-radius:4px; border:none; background:#1e1f22; color:#dbdee1; font-size:14px; margin-bottom:16px; }
.caixa-modal textarea { min-height:70px; resize:vertical; }
.caixa-modal .grade-tipo-canal { display:flex; gap:8px; margin-bottom:16px; }
.caixa-modal .grade-tipo-canal label { display:flex; align-items:center; gap:6px; background:#1e1f22; padding:8px 12px;
                border-radius:6px; cursor:pointer; font-size:13px; color:#dbdee1; text-transform:none; font-weight:500; flex:1; margin:0; }
.caixa-modal .linha-checkbox { display:flex; align-items:center; gap:8px; margin-bottom:16px; }
.caixa-modal .linha-checkbox input { width:auto; margin:0; }
.caixa-modal .linha-checkbox label { margin:0; text-transform:none; font-size:13px; font-weight:500; }
.botao-primario-modal { width:100%; padding:11px; border-radius:4px; border:none; background:#5865f2; color:#fff; font-weight:600;
                cursor:pointer; font-size:14px; }
.botao-primario-modal:hover { background:#4752c4; }
.botao-primario-modal.perigoso { background:#da373c; }
.botao-primario-modal.perigoso:hover { background:#a12828; }
.linha-botoes-modal { display:flex; justify-content:flex-end; gap:8px; padding:12px 16px; background:#2b2d31; }
.linha-botoes-modal button { padding:10px 16px; border-radius:4px; border:none; font-weight:600; cursor:pointer; font-size:14px; }
.linha-botoes-modal .cancelar-modal { background:none; color:#dbdee1; }
.linha-botoes-modal .cancelar-modal:hover { text-decoration:underline; }
.mensagem-modal { font-size:12px; color:#3ba55c; margin-top:-8px; margin-bottom:12px; min-height:14px; }
.mensagem-modal.erro { color:#fa777c; }
.codigo-convite-caixa { background:#1e1f22; padding:12px; border-radius:6px; font-size:14px; color:#00a8fc; text-align:center;
                word-break:break-all; margin-bottom:12px; cursor:pointer; }
.abas-modal-topo { display:flex; gap:4px; padding:0 16px; border-bottom:1px solid #232428; overflow-x:auto; }
.abas-modal-topo button { background:none; border:none; color:#949ba4; padding:10px 12px; font-size:13px; font-weight:600; cursor:pointer;
                border-bottom:2px solid transparent; white-space:nowrap; }
.abas-modal-topo button.ativa { color:#fff; border-bottom-color:#5865f2; }
.secao-modal-tab { display:none; }
.secao-modal-tab.ativa { display:block; }

.linha-lista-modal { display:flex; align-items:center; gap:10px; padding:9px 4px; border-bottom:1px solid #3a3c41; font-size:13px; }
.linha-lista-modal img { width:32px; height:32px; border-radius:50%; object-fit:cover; }
.linha-lista-modal .info-linha { flex:1; min-width:0; }
.linha-lista-modal .info-linha .nome-linha { color:#dbdee1; font-weight:600; }
.linha-lista-modal .info-linha .sub-linha { color:#80848e; font-size:11.5px; }
.linha-lista-modal button { background:#404249; border:none; color:#dbdee1; font-size:11.5px; padding:6px 9px; border-radius:6px; cursor:pointer; margin-left:4px; }
.linha-lista-modal button.ativo-toggle { background:#3ba55c; color:#fff; }
.linha-lista-modal button.perigo-toggle { background:#da373c; color:#fff; }
.campo-busca-modal { margin-bottom:12px; }

.card-descoberta { display:flex; align-items:center; gap:12px; padding:10px; border-radius:8px; background:#232428; margin-bottom:8px; }
.card-descoberta img { width:44px; height:44px; border-radius:12px; object-fit:cover; background:#1e1f22; }
.card-descoberta .info-descoberta { flex:1; min-width:0; }
.card-descoberta .info-descoberta .nome-descoberta { color:#fff; font-weight:600; font-size:14px; }
.card-descoberta .info-descoberta .sub-descoberta { color:#949ba4; font-size:11.5px; }

.emoji-grade-modal { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:14px; }
.emoji-item-modal { display:flex; flex-direction:column; align-items:center; gap:4px; background:#1e1f22; padding:8px; border-radius:8px; width:74px; }
.emoji-item-modal img { width:32px; height:32px; object-fit:contain; }
.emoji-item-modal span { font-size:10.5px; color:#949ba4; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:100%; }
.emoji-item-modal button { background:none; border:none; color:#da373c; font-size:11px; cursor:pointer; }

/* Popover / modal de perfil de usuario */
.perfil-banner { height:90px; background:linear-gradient(135deg,#5865f2,#7289da); border-radius:8px 8px 0 0; background-size:cover; background-position:center; }
.perfil-avatar-wrap { margin:-38px 0 0 16px; width:80px; height:80px; border-radius:50%; border:6px solid #313338; background:#1e1f22; }
.perfil-avatar-wrap img { width:100%; height:100%; border-radius:50%; object-fit:cover; }
.perfil-corpo-info { padding:44px 16px 16px; }
.perfil-corpo-info .nome-perfil-popover { color:#fff; font-size:19px; font-weight:700; }
.perfil-corpo-info .id-perfil-popover { color:#949ba4; font-size:12px; margin-top:2px; }
.perfil-corpo-info .bio-perfil-popover { color:#dbdee1; font-size:13px; margin-top:14px; white-space:pre-wrap; }
.perfil-corpo-info .desde-perfil-popover { color:#949ba4; font-size:11.5px; margin-top:14px; border-top:1px solid #3a3c41; padding-top:12px; }
.tags-especiais-perfil { display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }
.tag-especial-perfil { font-size:10.5px; font-weight:700; padding:3px 8px; border-radius:10px; }
.tag-especial-perfil.premium { background:#f47fff33; color:#f47fff; }
.tag-especial-perfil.admin { background:#fee75c33; color:#fee75c; }

/* Chamada de voz/video em DM */
.modal-chamada-dm { display:none; position:fixed; inset:0; background:#000000f2; z-index:210; align-items:center; justify-content:center;
               flex-direction:column; color:#fff; text-align:center; padding:16px; }
.modal-chamada-dm.aberto { display:flex; }
.modal-chamada-dm img.avatar-chamada { width:96px; height:96px; border-radius:50%; object-fit:cover; margin-bottom:16px; border:2px solid #ffffff33; z-index:2; }
.modal-chamada-dm .status-chamada-dm { color:#949ba4; margin-bottom:30px; font-size:14px; z-index:2; }
.modal-chamada-dm .botoes-chamada-dm { display:flex; gap:18px; flex-wrap:wrap; justify-content:center; z-index:2; }
.botao-chamada-circulo { width:56px; height:56px; border-radius:50%; border:none; font-size:22px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
.botao-chamada-circulo.aceitar { background:#3ba55c; color:#000; }
.botao-chamada-circulo.recusar, .botao-chamada-circulo.encerrar { background:#da373c; color:#fff; }
.botao-chamada-circulo.neutro { background:#404249; color:#fff; }
.video-remoto-chamada-dm { display:none; width:100%; height:100%; position:absolute; inset:0; object-fit:cover; background:#000; }
.video-remoto-chamada-dm.ativo { display:block; }
.video-local-chamada-dm { display:none; position:absolute; bottom:130px; right:20px; width:110px; height:150px; border-radius:12px; object-fit:cover; border:2px solid #ffffff44; z-index:2; background:#111; }
.video-local-chamada-dm.ativo { display:block; }

.menu-mobile-toggle { display:none; }

@media (max-width: 860px) {
  #segundaColuna { position:fixed; left:72px; top:0; bottom:0; z-index:40; transition:margin-left .2s ease; }
  #segundaColuna.recolhida { margin-left:-240px; }
  .menu-mobile-toggle { display:flex; cursor:pointer; color:#dbdee1; font-size:20px; align-items:center; }
  #painelMembros.aberto { position:fixed; right:0; top:0; bottom:0; z-index:41; }
}
"""

CORPO_APP_SHELL = """
<div id="appShell">
  <div id="railServidores"></div>
  <div id="segundaColuna"></div>
  <div id="areaPrincipal">
    <div class="topo-principal" id="topoPrincipal"></div>
    <div class="corpo-principal" id="corpoPrincipal"></div>
  </div>
  <div id="painelMembros"></div>
</div>

<!-- Modal: adicionar amigo -->
<div class="fundo-modal" id="modalAddAmigo">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Adicionar amigo</h2><p>Voce pode adicionar amigos pelo apelido ou pelo ID numerico deles.</p></div>
    <div class="corpo-modal">
      <label>Apelido ou #ID</label>
      <input type="text" id="campoAddAmigo" placeholder="ex: joana ou #482">
      <div class="mensagem-modal" id="msgAddAmigo"></div>
      <button class="botao-primario-modal" onclick="enviarPedidoAmizade()">Enviar pedido de amizade</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalAddAmigo')">Fechar</button></div>
  </div>
</div>

<!-- Modal: criar servidor -->
<div class="fundo-modal" id="modalCriarServidor">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Criar um servidor</h2><p>Seu servidor e onde voce e seus amigos vao passar o tempo.</p></div>
    <div class="corpo-modal">
      <label>Icone (opcional)</label>
      <input type="file" id="iconeCriarServidor" accept="image/*">
      <label>Nome do servidor</label>
      <input type="text" id="nomeCriarServidor" maxlength="60" placeholder="Servidor do(a) fulano">
      <div class="mensagem-modal" id="msgCriarServidor"></div>
      <button class="botao-primario-modal" onclick="criarServidor()">Criar</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalCriarServidor')">Cancelar</button></div>
  </div>
</div>

<!-- Modal: entrar em servidor -->
<div class="fundo-modal" id="modalEntrarServidor">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Entrar em um servidor</h2><p>Cole um codigo de convite abaixo.</p></div>
    <div class="corpo-modal">
      <label>Codigo de convite</label>
      <input type="text" id="codigoEntrarServidor" placeholder="ex: aZ8kLp2Q">
      <div class="mensagem-modal" id="msgEntrarServidor"></div>
      <button class="botao-primario-modal" onclick="entrarServidor()">Entrar</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalEntrarServidor')">Cancelar</button></div>
  </div>
</div>

<!-- Modal: descobrir servidores publicos -->
<div class="fundo-modal" id="modalDescobrir">
  <div class="caixa-modal grande">
    <div class="topo-modal"><h2>Descobrir servidores</h2><p>Servidores que os donos marcaram como publicos.</p></div>
    <div class="corpo-modal"><div id="listaDescoberta"></div></div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalDescobrir')">Fechar</button></div>
  </div>
</div>

<!-- Modal: criar canal -->
<div class="fundo-modal" id="modalCriarCanal">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Criar canal</h2></div>
    <div class="corpo-modal">
      <label>Tipo de canal</label>
      <div class="grade-tipo-canal">
        <label><input type="radio" name="tipoCanalNovo" value="texto" checked> # Texto</label>
        <label><input type="radio" name="tipoCanalNovo" value="voz"> &#128266; Voz</label>
      </div>
      <label>Nome do canal</label>
      <input type="text" id="nomeCriarCanal" maxlength="40" placeholder="novo-canal">
      <label>Categoria (opcional)</label>
      <select id="categoriaCriarCanal"><option value="">Sem categoria</option></select>
      <div class="mensagem-modal" id="msgCriarCanal"></div>
      <button class="botao-primario-modal" onclick="criarCanal()">Criar canal</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalCriarCanal')">Cancelar</button></div>
  </div>
</div>

<!-- Modal: editar canal -->
<div class="fundo-modal" id="modalEditarCanal">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Editar canal</h2></div>
    <div class="corpo-modal">
      <label>Nome</label>
      <input type="text" id="editarNomeCanal" maxlength="40">
      <label>Categoria</label>
      <select id="editarCategoriaCanal"><option value="">Sem categoria</option></select>
      <label>Topico (mostrado no topo do canal)</label>
      <input type="text" id="editarTopicoCanal" maxlength="200">
      <label>Modo lento (segundos de espera entre mensagens, 0 = desligado)</label>
      <input type="number" id="editarSlowmodeCanal" min="0" max="600" value="0">
      <div class="mensagem-modal" id="msgEditarCanal"></div>
      <button class="botao-primario-modal" onclick="salvarEdicaoCanal()">Salvar</button>
      <button class="botao-primario-modal perigoso" style="margin-top:10px;" onclick="excluirCanalAtualEditado()">Excluir canal</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalEditarCanal')">Cancelar</button></div>
  </div>
</div>

<!-- Modal: criar categoria -->
<div class="fundo-modal" id="modalCriarCategoria">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Criar categoria</h2></div>
    <div class="corpo-modal">
      <label>Nome da categoria</label>
      <input type="text" id="nomeCriarCategoria" maxlength="40" placeholder="NOVA CATEGORIA">
      <div class="mensagem-modal" id="msgCriarCategoria"></div>
      <button class="botao-primario-modal" onclick="criarCategoria()">Criar</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalCriarCategoria')">Cancelar</button></div>
  </div>
</div>

<!-- Modal: convite do servidor -->
<div class="fundo-modal" id="modalConviteServidor">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Convidar amigos</h2><p>Compartilhe este codigo. Qualquer pessoa pode usa-lo para entrar.</p></div>
    <div class="corpo-modal">
      <div class="codigo-convite-caixa" id="codigoConviteTexto" onclick="copiarCodigoConvite()">------</div>
      <button class="botao-primario-modal" onclick="copiarCodigoConvite()">Copiar codigo</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalConviteServidor')">Fechar</button></div>
  </div>
</div>

<!-- Modal: configuracoes do servidor (com abas) -->
<div class="fundo-modal" id="modalConfigServidor">
  <div class="caixa-modal grande">
    <div class="topo-modal"><h2>Configuracoes do servidor</h2></div>
    <div class="abas-modal-topo">
      <button class="ativa" onclick="mudarAbaConfigServidor('geral', this)">Visao geral</button>
      <button onclick="mudarAbaConfigServidor('emojis', this)">Emojis</button>
      <button onclick="mudarAbaConfigServidor('membros', this)">Membros</button>
      <button onclick="mudarAbaConfigServidor('banidos', this)">Banidos</button>
    </div>
    <div class="corpo-modal">
      <div class="secao-modal-tab ativa" id="tabConfigGeral">
        <label>Nome</label>
        <input type="text" id="editarNomeServidor" maxlength="60">
        <label>Descricao</label>
        <textarea id="editarDescricaoServidor" placeholder="Do que se trata o servidor?"></textarea>
        <label>Novo icone</label>
        <input type="file" id="editarIconeServidor" accept="image/*">
        <div class="linha-checkbox"><input type="checkbox" id="editarPublicoServidor"><label for="editarPublicoServidor">Listar na descoberta publica de servidores</label></div>
        <div class="mensagem-modal" id="msgConfigServidor"></div>
        <button class="botao-primario-modal" onclick="salvarConfigServidor()">Salvar alteracoes</button>
      </div>
      <div class="secao-modal-tab" id="tabConfigEmojis">
        <div id="infoSlotEmoji" style="color:#949ba4; font-size:12px; margin-bottom:10px;"></div>
        <div class="emoji-grade-modal" id="gradeEmojisServidor"></div>
        <label>Nome do novo emoji (sem espacos)</label>
        <input type="text" id="nomeNovoEmoji" maxlength="24" placeholder="ex: risada">
        <label>Imagem</label>
        <input type="file" id="arquivoNovoEmoji" accept="image/*">
        <div class="mensagem-modal" id="msgEmojiServidor"></div>
        <button class="botao-primario-modal" onclick="criarEmojiServidor()">Adicionar emoji</button>
      </div>
      <div class="secao-modal-tab" id="tabConfigMembros">
        <div id="listaMembrosConfig"></div>
      </div>
      <div class="secao-modal-tab" id="tabConfigBanidos">
        <div id="listaBanidosConfig"></div>
      </div>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalConfigServidor')">Fechar</button></div>
  </div>
</div>

<!-- Modal: cargos do servidor -->
<div class="fundo-modal" id="modalCargosServidor">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Cargos do servidor</h2></div>
    <div class="corpo-modal">
      <label>Criar novo cargo</label>
      <div style="display:flex; gap:8px; margin-bottom:16px;">
        <input type="text" id="nomeNovoCargo" placeholder="nome do cargo" style="margin:0;">
        <input type="color" id="corNovoCargo" value="#99aab5" style="width:44px; height:40px; border:none; border-radius:4px; padding:0; cursor:pointer;">
        <button class="botao-primario-modal" style="width:auto; padding:0 16px;" onclick="criarCargo()">Criar</button>
      </div>
      <div id="listaCargosModal"></div>
      <div class="mensagem-modal" id="msgCargosServidor"></div>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalCargosServidor')">Fechar</button></div>
  </div>
</div>

<!-- Modal: mensagens fixadas -->
<div class="fundo-modal" id="modalFixadas">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Mensagens fixadas</h2></div>
    <div class="corpo-modal"><div id="listaFixadas"></div></div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalFixadas')">Fechar</button></div>
  </div>
</div>

<!-- Modal: apelido no servidor -->
<div class="fundo-modal" id="modalApelidoServidor">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Alterar apelido</h2><p>Esse apelido so aparece dentro deste servidor.</p></div>
    <div class="corpo-modal">
      <label>Apelido no servidor</label>
      <input type="text" id="campoApelidoServidor" maxlength="32" placeholder="deixe vazio para usar seu apelido normal">
      <div class="mensagem-modal" id="msgApelidoServidor"></div>
      <button class="botao-primario-modal" onclick="salvarApelidoServidor()">Salvar</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalApelidoServidor')">Fechar</button></div>
  </div>
</div>

<!-- Modal: perfil / conta -->
<div class="fundo-modal" id="modalPerfil">
  <div class="caixa-modal">
    <div class="topo-modal"><h2>Meu perfil</h2></div>
    <div class="corpo-modal">
      <label>Nova foto de perfil</label>
      <input type="file" id="arquivoNovoAvatar" accept="image/*">
      <label id="rotuloBannerPerfil">Banner de perfil (recurso liberado pelo administrador)</label>
      <input type="file" id="arquivoNovoBanner" accept="image/*">
      <label>Status / recado</label>
      <input type="text" id="novoStatusTexto" maxlength="80" placeholder="Diga algo sobre voce">
      <label>Bio</label>
      <textarea id="novaBioPerfil" maxlength="300" placeholder="Fale sobre voce"></textarea>
      <div class="mensagem-modal" id="msgPerfil"></div>
      <button class="botao-primario-modal" onclick="salvarPerfil()">Salvar</button>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalPerfil')">Fechar</button></div>
  </div>
</div>

<!-- Modal: ver perfil de outra pessoa -->
<div class="fundo-modal" id="modalVerPerfil">
  <div class="caixa-modal" id="caixaVerPerfil" style="padding:0;"></div>
</div>

<!-- Modal: painel do administrador -->
<div class="fundo-modal" id="modalAdmin">
  <div class="caixa-modal grande">
    <div class="topo-modal"><h2>Painel do administrador</h2><p>Nenhum recurso aqui envolve pagamento - tudo e liberado manualmente por voce.</p></div>
    <div class="abas-modal-topo">
      <button class="ativa" onclick="mudarAbaAdmin('usuarios', this)">Usuarios</button>
      <button onclick="mudarAbaAdmin('servidores', this)">Servidores</button>
    </div>
    <div class="corpo-modal">
      <div class="secao-modal-tab ativa" id="tabAdminUsuarios">
        <div class="campo-busca-modal"><input type="text" id="buscaAdminUsuarios" placeholder="Buscar por apelido..." oninput="renderizarAdminUsuarios()"></div>
        <div id="listaAdminUsuarios"></div>
      </div>
      <div class="secao-modal-tab" id="tabAdminServidores">
        <div class="campo-busca-modal"><input type="text" id="buscaAdminServidores" placeholder="Buscar por nome..." oninput="renderizarAdminServidores()"></div>
        <div id="listaAdminServidores"></div>
      </div>
    </div>
    <div class="linha-botoes-modal"><button class="cancelar-modal" onclick="fecharModal('modalAdmin')">Fechar</button></div>
  </div>
</div>

<!-- Chamada de voz/video em DM -->
<div class="modal-chamada-dm" id="modalChamadaDM">
  <video class="video-remoto-chamada-dm" id="videoRemotoDM" autoplay playsinline></video>
  <video class="video-local-chamada-dm" id="videoLocalDM" autoplay playsinline muted></video>
  <img class="avatar-chamada" id="avatarChamadaDM" src="">
  <div style="font-size:18px; font-weight:bold; z-index:2;" id="nomeChamadaDM"></div>
  <div class="status-chamada-dm" id="statusChamadaDM">Chamando...</div>
  <div class="botoes-chamada-dm" id="botoesChamadaDM"></div>
  <audio id="audioRemotoDM" autoplay></audio>
</div>
"""


SCRIPT_APP = r"""
<script>
const ICE_SERVERS = %%ICE_SERVERS%%;
const MEU_USUARIO = %%USUARIO_JSON%%;
const EMOJIS_REACAO = %%EMOJIS_REACAO%%;

let estado = {
    contexto: null, abaAmigos: 'online', dmAtual: null, servidorAtual: null,
    canalAtual: null, tipoCanalAtual: null, nomeCanalAtual: '', servidoresCache: [],
    detalheServidorCache: null, souAdminGlobal: false,
};

function escaparHtml(t) { const d = document.createElement('div'); d.textContent = (t == null ? '' : String(t)); return d.innerHTML; }
function fecharModal(id) { document.getElementById(id).classList.remove('aberto'); }
function abrirModal(id) { document.getElementById(id).classList.add('aberto'); }
function aplicarEmojisTexto(texto, listaEmojis) {
    let html = escaparHtml(texto);
    (listaEmojis || []).forEach(e => {
        const re = new RegExp(':' + e.nome.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ':', 'g');
        html = html.replace(re, '<img class="emoji-custom" src="' + e.url + '" title="' + escaparHtml(e.nome) + '">');
    });
    return html;
}

// ---------------------------------------------------------------
// Rail de servidores
// ---------------------------------------------------------------
async function carregarRailServidores() {
    const r = await fetch('/api/servidores');
    const servidores = await r.json();
    estado.servidoresCache = servidores;
    const naoLidos = await (await fetch('/api/nao_lidos')).json();
    const rail = document.getElementById('railServidores');
    let html = `<div class="rail-item dm-icone ${estado.contexto !== 'servidor' ? 'ativo' : ''}" onclick="abrirVisaoAmigos()" title="Mensagens diretas"><span class="pastilha"></span>&#128172;${(naoLidos.dms||[]).length ? '<span class="ponto-nao-lido"></span>' : ''}</div>`;
    html += '<div class="rail-separador"></div>';
    servidores.forEach(s => {
        const ativo = estado.servidorAtual === s.id ? 'ativo' : '';
        const naoLido = (naoLidos.servidores || []).includes(s.id) ? '<span class="ponto-nao-lido"></span>' : '';
        html += `<div class="rail-item ${ativo} ${s.impulsionado ? 'impulsionado' : ''}" onclick="abrirServidor(${s.id})" title="${escaparHtml(s.nome)}"><span class="pastilha"></span>${s.icone ? '<img src="'+s.icone+'">' : escaparHtml(s.nome.slice(0,2).toUpperCase())}${naoLido}</div>`;
    });
    html += '<div class="rail-separador"></div>';
    html += `<div class="rail-item adicionar" onclick="abrirModal('modalCriarServidor')" title="Adicionar servidor">+</div>`;
    html += `<div class="rail-item adicionar" style="color:#00a8fc;" onclick="abrirModal('modalEntrarServidor')" title="Entrar com codigo">&#128279;</div>`;
    html += `<div class="rail-item adicionar" style="color:#949ba4;" onclick="abrirDescoberta()" title="Descobrir servidores publicos">&#128506;</div>`;
    if (estado.souAdminGlobal) {
        html += '<div class="rail-separador"></div>';
        html += `<div class="rail-item admin-icone" onclick="abrirPainelAdmin()" title="Painel do administrador">&#128737;</div>`;
    }
    rail.innerHTML = html;
}

// ---------------------------------------------------------------
// Rodape com meu usuario
// ---------------------------------------------------------------
let meuPerfilCache = null;
async function carregarRodapeUsuario() {
    const r = await fetch('/api/me');
    const d = await r.json();
    meuPerfilCache = d;
    estado.souAdminGlobal = d.eh_admin;
    const div = document.createElement('div');
    div.id = 'rodapeUsuarioFixo';
    div.className = 'rodape-usuario';
    div.innerHTML = `
        <img src="${d.avatar}" onclick="abrirModalPerfil()">
        <div class="info-rodape" onclick="abrirModalPerfil()">
            <div class="nome-rodape">${escaparHtml(d.usuario)}${d.premium?'<span class="selo-impulso-nome">&#10022;</span>':''}${d.eh_admin?'<span class="selo-admin-nome">&#128737;</span>':''}</div>
            <div class="id-rodape">#${d.id_publico}${d.status_texto ? ' - ' + escaparHtml(d.status_texto) : ''}</div>
        </div>
        <button class="botao-sair-rodape" onclick="window.location.href='/sair'" title="Sair">&#9211;</button>`;
    return div;
}
function abrirModalPerfil() {
    if (!meuPerfilCache) return;
    document.getElementById('novoStatusTexto').value = meuPerfilCache.status_texto || '';
    document.getElementById('novaBioPerfil').value = meuPerfilCache.bio || '';
    document.getElementById('msgPerfil').textContent = '';
    const rotuloBanner = document.getElementById('rotuloBannerPerfil');
    const inputBanner = document.getElementById('arquivoNovoBanner');
    if (meuPerfilCache.premium) { rotuloBanner.textContent = 'Banner de perfil'; inputBanner.disabled = false; }
    else { rotuloBanner.textContent = 'Banner de perfil (peça para um administrador liberar este recurso pra voce)'; inputBanner.disabled = true; }
    abrirModal('modalPerfil');
}
async function salvarPerfil() {
    const msg = document.getElementById('msgPerfil');
    msg.className = 'mensagem-modal'; msg.textContent = 'Salvando...';
    const form = new FormData();
    form.append('status_texto', document.getElementById('novoStatusTexto').value.trim());
    form.append('bio', document.getElementById('novaBioPerfil').value.trim());
    const arquivoAvatar = document.getElementById('arquivoNovoAvatar').files[0];
    if (arquivoAvatar) form.append('avatar', arquivoAvatar);
    const arquivoBanner = document.getElementById('arquivoNovoBanner').files[0];
    if (arquivoBanner && !document.getElementById('arquivoNovoBanner').disabled) form.append('banner', arquivoBanner);
    const r = await fetch('/api/perfil/editar', { method:'POST', body: form });
    const d = await r.json();
    if (d.ok) { msg.textContent = 'Salvo!'; setTimeout(() => { fecharModal('modalPerfil'); montarColunaLateral(); }, 500); }
    else { msg.className = 'mensagem-modal erro'; msg.textContent = d.erro || 'Erro ao salvar.'; }
}
async function abrirPerfilDe(usuario) {
    const r = await fetch('/api/usuarios/' + encodeURIComponent(usuario) + '/perfil');
    const p = await r.json();
    const desde = new Date(p.criado_em).toLocaleDateString('pt-BR', { day:'2-digit', month:'long', year:'numeric' });
    const tags = [];
    if (p.premium) tags.push('<span class="tag-especial-perfil premium">&#10022; Recurso extra liberado</span>');
    if (p.eh_admin) tags.push('<span class="tag-especial-perfil admin">&#128737; Administrador</span>');
    document.getElementById('caixaVerPerfil').innerHTML = `
        <div class="perfil-banner" style="${p.banner ? 'background-image:url(\''+p.banner+'\')' : ''}"></div>
        <div class="perfil-avatar-wrap"><img src="${p.avatar}"></div>
        <div class="perfil-corpo-info">
            <div class="nome-perfil-popover">${escaparHtml(p.usuario)}</div>
            <div class="id-perfil-popover">#${p.id_publico} ${p.online ? '- Online' : ''}</div>
            <div class="tags-especiais-perfil">${tags.join('')}</div>
            ${p.bio ? '<div class="bio-perfil-popover">'+escaparHtml(p.bio)+'</div>' : ''}
            <div class="desde-perfil-popover">Membro do NOVO GG desde ${desde}</div>
        </div>`;
    abrirModal('modalVerPerfil');
}

// ---------------------------------------------------------------
// Navegacao geral
// ---------------------------------------------------------------
function abrirVisaoAmigos() {
    estado.contexto = 'amigos'; estado.servidorAtual = null; estado.canalAtual = null; estado.dmAtual = null;
    fecharPainelMembros(); montarTudo();
}
async function abrirDM(usuario) {
    estado.contexto = 'dm'; estado.dmAtual = usuario; estado.servidorAtual = null; estado.canalAtual = null;
    fecharPainelMembros(); montarTudo();
}
async function abrirServidor(servidorId) {
    estado.contexto = 'servidor'; estado.servidorAtual = servidorId; estado.canalAtual = null; estado.tipoCanalAtual = null; estado.dmAtual = null;
    await montarTudo();
}
async function montarTudo() {
    await carregarRailServidores(); await montarColunaLateral(); montarAreaPrincipal();
}

// ---------------------------------------------------------------
// Coluna lateral: amigos OU canais do servidor
// ---------------------------------------------------------------
async function montarColunaLateral() {
    const coluna = document.getElementById('segundaColuna');
    if (estado.contexto === 'servidor' && estado.servidorAtual) { await montarColunaServidor(coluna); }
    else { await montarColunaAmigos(coluna); }
    const rodape = await carregarRodapeUsuario();
    coluna.appendChild(rodape);
}
async function montarColunaAmigos(coluna) {
    coluna.innerHTML = `
      <div class="topo-coluna">Encontrar ou iniciar uma conversa</div>
      <div class="busca-dm"><input type="text" id="buscaDmCampo" placeholder="Buscar amigo..." oninput="filtrarListaAmigos()"></div>
      <div class="abas-social">
        <div class="item-social ${estado.abaAmigos==='online'?'ativo':''}" onclick="mudarAbaAmigos('online')">Amigos online</div>
        <div class="item-social ${estado.abaAmigos==='todos'?'ativo':''}" onclick="mudarAbaAmigos('todos')">Todos os amigos</div>
        <div class="item-social ${estado.abaAmigos==='pendentes'?'ativo':''}" onclick="mudarAbaAmigos('pendentes')">Pendentes</div>
        <div class="item-social ${estado.abaAmigos==='bloqueados'?'ativo':''}" onclick="mudarAbaAmigos('bloqueados')">Bloqueados</div>
        <div class="item-social destaque-add" onclick="abrirModal('modalAddAmigo')">Adicionar amigo</div>
      </div>
      <div class="lista-lateral" id="listaLateralAmigos"></div>`;
    await renderizarListaLateralAmigos();
}
function mudarAbaAmigos(aba) { estado.abaAmigos = aba; montarColunaLateral(); montarAreaPrincipal(); }
async function renderizarListaLateralAmigos() {
    const div = document.getElementById('listaLateralAmigos');
    if (!div) return;
    const amigos = await (await fetch('/api/amigos')).json();
    const naoLidos = await (await fetch('/api/nao_lidos')).json();
    let lista = amigos;
    if (estado.abaAmigos === 'online') lista = amigos.filter(a => a.online);
    if (!lista.length) { div.innerHTML = '<div class="vazio-lista-lateral">Ninguem por aqui ainda.</div>'; return; }
    div.innerHTML = '<div class="linha-secao">' + (estado.abaAmigos === 'online' ? 'ONLINE' : 'TODOS OS AMIGOS') + ' - ' + lista.length + '</div>';
    lista.forEach(a => {
        const item = document.createElement('div');
        item.className = 'item-amigo' + (estado.dmAtual === a.usuario ? ' ativo' : '');
        item.onclick = () => abrirDM(a.usuario);
        const temNaoLido = (naoLidos.dms || []).includes(a.usuario);
        item.innerHTML = `
          <div class="avatar-com-status ${a.premium?'premium':''}"><img src="${a.avatar}"><span class="bolinha-status ${a.online?'online':''}"></span></div>
          <div class="info-amigo"><div class="nome-amigo">${escaparHtml(a.usuario)}</div><div class="sub-amigo">${a.online?'Online':'Offline'}</div></div>
          ${temNaoLido ? '<span class="ponto-nao-lido-dm"></span>' : ''}`;
        div.appendChild(item);
    });
}
function filtrarListaAmigos() {
    const termo = document.getElementById('buscaDmCampo').value.toLowerCase();
    document.querySelectorAll('#listaLateralAmigos .item-amigo').forEach(el => {
        const nome = el.querySelector('.nome-amigo').textContent.toLowerCase();
        el.style.display = nome.includes(termo) ? 'flex' : 'none';
    });
}

async function montarColunaServidor(coluna) {
    const servidor = await (await fetch('/api/servidores/' + estado.servidorAtual)).json();
    if (!servidor.ok) { coluna.innerHTML = '<div class="vazio-lista-lateral">Nao foi possivel carregar o servidor.</div>'; return; }
    estado.detalheServidorCache = servidor;
    const podeGerenciar = servidor.pode_gerenciar;
    coluna.innerHTML = `
      <div class="cabecalho-servidor-topo" onclick="alternarMenuServidor(event)">
        ${escaparHtml(servidor.nome)} ${servidor.verificado?'<span class="selo-verificado-nome">&#9989;</span>':''} ${servidor.impulsionado?'<span class="selo-impulso-nome">&#10022;</span>':''}
        <span class="seta-servidor">&#9662;</span>
        <div class="menu-flutuante-servidor" id="menuFlutuanteServidor">
          <div class="item-menu-flutuante" onclick="mostrarConvite()">&#128279; Convidar pessoas</div>
          <div class="item-menu-flutuante" onclick="abrirModalApelido()">&#127917; Alterar meu apelido aqui</div>
          <div class="item-menu-flutuante" onclick="abrirFixadas()">&#128204; Mensagens fixadas</div>
          ${podeGerenciar ? '<div class="item-menu-flutuante" onclick="abrirModal(\'modalCriarCategoria\')">&#128193; Criar categoria</div>' : ''}
          ${podeGerenciar ? '<div class="item-menu-flutuante" onclick="abrirConfigServidor()">&#9881; Configuracoes do servidor</div>' : ''}
          ${podeGerenciar ? '<div class="item-menu-flutuante" onclick="abrirModalCargos()">&#127991; Cargos</div>' : ''}
          <div class="item-menu-flutuante" onclick="alternarPainelMembros()">&#128101; Ver membros</div>
          ${podeGerenciar ? '<div class="item-menu-flutuante perigo" onclick="excluirServidorAtual()">&#128465; Excluir servidor</div>' : '<div class="item-menu-flutuante perigo" onclick="sairDoServidorAtual()">&#8618; Sair do servidor</div>'}
        </div>
      </div>
      <div class="lista-lateral" id="listaCanaisServidor" style="padding-top:4px;"></div>`;
    renderizarCanais(servidor.categorias, servidor.canais, podeGerenciar);
}
function alternarMenuServidor(ev) { ev.stopPropagation(); document.getElementById('menuFlutuanteServidor').classList.toggle('aberto'); }
document.addEventListener('click', (ev) => {
    const menu = document.getElementById('menuFlutuanteServidor');
    if (menu && menu.classList.contains('aberto') && !ev.target.closest('.cabecalho-servidor-topo')) menu.classList.remove('aberto');
});

function renderizarCanais(categorias, canais, podeGerenciar) {
    const div = document.getElementById('listaCanaisServidor');
    const naoLidos = window._naoLidosCache || {};
    function linhaCanal(c) {
        const ativo = estado.canalAtual === c.id ? 'ativo' : '';
        const iconeGear = podeGerenciar ? `<span class="engrenagem-canal" onclick="event.stopPropagation();abrirEditarCanal(${c.id})">&#9881;</span>` : '';
        if (c.tipo === 'texto') {
            const naoLido = (naoLidos.canais || []).includes(c.id) && estado.canalAtual !== c.id ? '<span class="ponto-nao-lido-canal"></span>' : '';
            return `<div class="item-canal-servidor ${ativo}" onclick="abrirCanal(${c.id},'texto','${escaparHtml(c.nome)}')"># ${escaparHtml(c.nome)}${naoLido}${iconeGear}</div>`;
        }
        return `<div class="item-canal-servidor ${ativo}" id="canalvoz-${c.id}" onclick="abrirCanal(${c.id},'voz','${escaparHtml(c.nome)}')">&#128266; ${escaparHtml(c.nome)}<span class="contagem-voz"></span>${iconeGear}</div>`;
    }
    let html = '';
    const semCategoria = canais.filter(c => !c.categoria_id);
    semCategoria.forEach(c => { html += linhaCanal(c); });
    categorias.forEach(cat => {
        const iconesCategoria = podeGerenciar ? `<span class="add-canal-btn" title="Renomear categoria" onclick="renomearCategoria(${cat.id},'${escaparHtml(cat.nome)}')">&#9998;</span><span class="add-canal-btn" onclick="abrirModalCriarCanalComCategoria(${cat.id})">+</span>` : '';
        html += `<div class="grupo-canais-titulo">${escaparHtml(cat.nome)} ${iconesCategoria}</div>`;
        canais.filter(c => c.categoria_id === cat.id).forEach(c => { html += linhaCanal(c); });
    });
    html += `<div class="grupo-canais-titulo">Sem categoria ${podeGerenciar ? '<span class="add-canal-btn" onclick="abrirModalCriarCanalComCategoria(null)">+</span>' : ''}</div>`;
    div.innerHTML = html || '<div class="vazio-lista-lateral">Nenhum canal ainda.</div>';
}

let categoriaSelecionadaParaNovoCanal = null;
function abrirModalCriarCanalComCategoria(categoriaId) {
    categoriaSelecionadaParaNovoCanal = categoriaId;
    const select = document.getElementById('categoriaCriarCanal');
    select.innerHTML = '<option value="">Sem categoria</option>' + (estado.detalheServidorCache.categorias || []).map(c => `<option value="${c.id}">${escaparHtml(c.nome)}</option>`).join('');
    if (categoriaId) select.value = categoriaId;
    document.getElementById('nomeCriarCanal').value = '';
    document.getElementById('msgCriarCanal').textContent = '';
    abrirModal('modalCriarCanal');
}
async function criarCanal() {
    const nome = document.getElementById('nomeCriarCanal').value.trim();
    const tipo = document.querySelector('input[name=tipoCanalNovo]:checked').value;
    const categoriaId = document.getElementById('categoriaCriarCanal').value || null;
    const msg = document.getElementById('msgCriarCanal');
    if (!nome) { msg.className='mensagem-modal erro'; msg.textContent = 'Digite um nome.'; return; }
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/canais/criar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ nome, tipo, categoria_id: categoriaId }) });
    const d = await r.json();
    if (!d.ok) { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; return; }
    fecharModal('modalCriarCanal'); montarColunaLateral();
}
async function criarCategoria() {
    const nome = document.getElementById('nomeCriarCategoria').value.trim();
    const msg = document.getElementById('msgCriarCategoria');
    if (!nome) { msg.className='mensagem-modal erro'; msg.textContent = 'Digite um nome.'; return; }
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/categorias/criar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ nome }) });
    const d = await r.json();
    if (!d.ok) { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; return; }
    document.getElementById('nomeCriarCategoria').value = '';
    fecharModal('modalCriarCategoria'); montarColunaLateral();
}
async function renomearCategoria(categoriaId, nomeAtual) {
    const novoNome = prompt('Novo nome da categoria:', nomeAtual);
    if (novoNome === null) return;
    const nome = novoNome.trim();
    if (!nome) return;
    const r = await fetch('/api/categorias/' + categoriaId + '/editar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ nome }) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || 'Nao foi possivel renomear.'); return; }
    montarColunaLateral();
}
let canalEmEdicaoId = null;
function abrirEditarCanal(canalId) {
    const canal = (estado.detalheServidorCache.canais || []).find(c => c.id === canalId);
    if (!canal) return;
    canalEmEdicaoId = canalId;
    document.getElementById('editarNomeCanal').value = canal.nome;
    const selectCategoria = document.getElementById('editarCategoriaCanal');
    selectCategoria.innerHTML = '<option value="">Sem categoria</option>' + (estado.detalheServidorCache.categorias || []).map(c => `<option value="${c.id}">${escaparHtml(c.nome)}</option>`).join('');
    selectCategoria.value = canal.categoria_id || '';
    document.getElementById('editarTopicoCanal').value = canal.topico || '';
    document.getElementById('editarSlowmodeCanal').value = canal.slowmode || 0;
    document.getElementById('msgEditarCanal').textContent = '';
    abrirModal('modalEditarCanal');
}
async function salvarEdicaoCanal() {
    const msg = document.getElementById('msgEditarCanal');
    const r = await fetch('/api/canais/' + canalEmEdicaoId + '/editar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
            nome: document.getElementById('editarNomeCanal').value.trim(),
            categoria_id: document.getElementById('editarCategoriaCanal').value || null,
            topico: document.getElementById('editarTopicoCanal').value.trim(),
            slowmode: parseInt(document.getElementById('editarSlowmodeCanal').value || '0', 10),
        }) });
    const d = await r.json();
    if (!d.ok) { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; return; }
    fecharModal('modalEditarCanal'); montarColunaLateral(); montarAreaPrincipal();
}
async function excluirCanalAtualEditado() {
    if (!confirm('Excluir este canal?')) return;
    await fetch('/api/canais/' + canalEmEdicaoId + '/excluir', { method:'POST' });
    fecharModal('modalEditarCanal');
    if (estado.canalAtual === canalEmEdicaoId) { estado.canalAtual = null; }
    montarColunaLateral(); montarAreaPrincipal();
}

// ---------------------------------------------------------------
// Area principal
// ---------------------------------------------------------------
let pollAtivo = null;
let pollDigitando = null;
function pararPoll() { if (pollAtivo) { clearInterval(pollAtivo); pollAtivo = null; } if (pollDigitando) { clearInterval(pollDigitando); pollDigitando = null; } }

async function montarAreaPrincipal() {
    pararPoll();
    const topo = document.getElementById('topoPrincipal');
    const corpo = document.getElementById('corpoPrincipal');
    fecharPainelMembros();

    if (estado.contexto === 'amigos') {
        if (estado.abaAmigos === 'adicionar') { topo.innerHTML = ''; corpo.innerHTML = ''; abrirModal('modalAddAmigo'); return; }
        const nomeAba = {online:'Amigos online', todos:'Todos os amigos', pendentes:'Pedidos pendentes', bloqueados:'Usuarios bloqueados'}[estado.abaAmigos] || 'Amigos';
        topo.innerHTML = `<span>&#128101;</span><span>${nomeAba}</span>`;
        corpo.innerHTML = `<div class="painel-amigos-central" id="painelAmigosCentral"></div>`;
        await renderizarPainelAmigosCentral();
        return;
    }

    if (estado.contexto === 'dm' && estado.dmAtual) {
        const alvo = await (await fetch('/api/usuarios/' + encodeURIComponent(estado.dmAtual))).json();
        topo.innerHTML = `<img class="avatar-topo" src="${alvo.avatar}" onclick="abrirPerfilDe('${escaparHtml(estado.dmAtual)}')" style="cursor:pointer;"><span onclick="abrirPerfilDe('${escaparHtml(estado.dmAtual)}')" style="cursor:pointer;">${escaparHtml(estado.dmAtual)}</span>
            <div class="acoes-topo"><span onclick="iniciarChamadaDM(false)" title="Ligar">&#128222;</span><span onclick="iniciarChamadaDM(true)" title="Chamada de video">&#128249;</span></div>`;
        corpo.innerHTML = `
          <div class="lista-mensagens" id="listaMensagensDM"></div>
          <div class="indicador-digitando" id="indicadorDigitandoDM"></div>
          <div class="area-input-mensagem"><div class="caixa-input-msg">
             <input type="text" id="campoMensagemDM" placeholder="Conversar com @${escaparHtml(estado.dmAtual)}" onkeydown="if(event.key==='Enter')enviarMensagemDM()" oninput="avisarDigitando('dm', '${escaparHtml(estado.dmAtual)}')">
             <button onclick="enviarMensagemDM()">&#10148;</button>
          </div></div>`;
        await carregarMensagensDM();
        pollAtivo = setInterval(carregarMensagensDM, 3000);
        pollDigitando = setInterval(() => atualizarIndicadorDigitando('dm', estado.dmAtual, 'indicadorDigitandoDM'), 2000);
        return;
    }

    if (estado.contexto === 'servidor' && estado.canalAtual) {
        const canalInfo = (estado.detalheServidorCache.canais || []).find(c => c.id === estado.canalAtual) || {};
        if (estado.tipoCanalAtual === 'texto') {
            topo.innerHTML = `<span># ${escaparHtml(estado.nomeCanalAtual||'')}</span>${canalInfo.topico ? '<span class="topico-canal-topo">'+escaparHtml(canalInfo.topico)+'</span>' : ''}
                <div class="acoes-topo"><span onclick="abrirFixadas()" title="Mensagens fixadas">&#128204;</span><span onclick="alternarPainelMembros()" title="Membros">&#128101;</span></div>`;
            corpo.innerHTML = `
              <div class="lista-mensagens" id="listaMensagensCanal"></div>
              <div class="indicador-digitando" id="indicadorDigitandoCanal"></div>
              <div class="area-input-mensagem"><div class="caixa-input-msg">
                 <input type="text" id="campoMensagemCanal" placeholder="Conversar em #${escaparHtml(estado.nomeCanalAtual||'')}" onkeydown="if(event.key==='Enter')enviarMensagemCanal()" oninput="avisarDigitando('canal', '${estado.canalAtual}')">
                 <button onclick="enviarMensagemCanal()">&#10148;</button>
              </div></div>`;
            await carregarMensagensCanal();
            pollAtivo = setInterval(carregarMensagensCanal, 3000);
            pollDigitando = setInterval(() => atualizarIndicadorDigitando('canal', estado.canalAtual, 'indicadorDigitandoCanal'), 2000);
        } else {
            topo.innerHTML = `<span>&#128266; ${escaparHtml(estado.nomeCanalAtual||'')}</span>
                <div class="acoes-topo"><span onclick="alternarPainelMembros()" title="Membros">&#128101;</span></div>`;
            corpo.innerHTML = `
              <div class="painel-voz-central">
                <div class="grade-voz-participantes" id="gradeVozParticipantes"></div>
                <div class="botoes-controle-voz" id="botoesControleVoz">
                  <button class="entrar" onclick="entrarCanalVoz(${estado.canalAtual})" title="Entrar">&#128222;</button>
                </div>
              </div>`;
            atualizarParticipantesVoz();
            pollAtivo = setInterval(atualizarParticipantesVoz, 3000);
        }
        await renderizarPainelMembrosServidor();
        return;
    }

    topo.innerHTML = '';
    corpo.innerHTML = `<div class="tela-boas-vindas"><div class="bolha-grande"><img src="/static/logo.svg" style="width:100%;height:100%;border-radius:50%;"></div><div>Escolha um amigo, servidor ou canal para comecar.</div></div>`;
}

async function abrirCanal(id, tipo, nome) {
    if (estado.tipoCanalAtual === 'voz' && estado.canalAtual !== id) await sairCanalVoz();
    estado.canalAtual = id; estado.tipoCanalAtual = tipo; estado.nomeCanalAtual = nome;
    montarColunaLateral(); montarAreaPrincipal();
}

// ---------------------------------------------------------------
// Painel de amigos (aba central)
// ---------------------------------------------------------------
async function renderizarPainelAmigosCentral() {
    const div = document.getElementById('painelAmigosCentral');
    if (!div) return;
    if (estado.abaAmigos === 'pendentes') {
        const dados = await (await fetch('/api/amigos/pendentes')).json();
        let html = '';
        if (dados.recebidos.length) {
            html += `<div class="contador-aba">PEDIDOS RECEBIDOS - ${dados.recebidos.length}</div>`;
            dados.recebidos.forEach(p => {
                html += `<div class="linha-pendente">
                    <div class="avatar-com-status"><img src="${p.avatar}"></div>
                    <div class="info-amigo"><div class="nome-amigo">${escaparHtml(p.usuario)}</div><div class="sub-amigo">Quer ser seu amigo</div></div>
                    <div class="acoes-item-amigo">
                        <button class="botao-mini-circulo" onclick="responderPedido('${escaparHtml(p.usuario)}', true)" title="Aceitar">&#10003;</button>
                        <button class="botao-mini-circulo recusar" onclick="responderPedido('${escaparHtml(p.usuario)}', false)" title="Recusar">&times;</button>
                    </div></div>`;
            });
        }
        if (dados.enviados.length) {
            html += `<div class="contador-aba" style="margin-top:20px;">PEDIDOS ENVIADOS - ${dados.enviados.length}</div>`;
            dados.enviados.forEach(p => {
                html += `<div class="linha-pendente">
                    <div class="avatar-com-status"><img src="${p.avatar}"></div>
                    <div class="info-amigo"><div class="nome-amigo">${escaparHtml(p.usuario)}</div><div class="sub-amigo">Pedido enviado</div></div>
                    <div class="acoes-item-amigo"><button class="botao-mini-circulo recusar" onclick="cancelarPedidoEnviado('${escaparHtml(p.usuario)}')" title="Cancelar">&times;</button></div></div>`;
            });
        }
        if (!dados.recebidos.length && !dados.enviados.length) html = '<div class="vazio-lista-lateral">Nenhum pedido pendente.</div>';
        div.innerHTML = html;
        return;
    }
    if (estado.abaAmigos === 'bloqueados') {
        const bloqueados = await (await fetch('/api/bloqueados')).json();
        if (!bloqueados.length) { div.innerHTML = '<div class="vazio-lista-lateral">Ninguem bloqueado.</div>'; return; }
        div.innerHTML = bloqueados.map(b => `<div class="linha-pendente">
            <div class="avatar-com-status"><img src="${b.avatar}"></div>
            <div class="info-amigo"><div class="nome-amigo">${escaparHtml(b.usuario)}</div></div>
            <div class="acoes-item-amigo"><button class="botao-mini-circulo" onclick="desbloquearUsuario('${escaparHtml(b.usuario)}')">Desbloquear</button></div></div>`).join('');
        return;
    }
    const amigos = await (await fetch('/api/amigos')).json();
    const lista = estado.abaAmigos === 'online' ? amigos.filter(a => a.online) : amigos;
    if (!lista.length) { div.innerHTML = '<div class="vazio-lista-lateral">Ninguem por aqui ainda. Adicione um amigo!</div>'; return; }
    div.innerHTML = `<div class="contador-aba">${estado.abaAmigos==='online'?'ONLINE':'TODOS OS AMIGOS'} - ${lista.length}</div>` +
        lista.map(a => `<div class="linha-pendente" style="cursor:pointer;" onclick="abrirDM('${escaparHtml(a.usuario)}')">
            <div class="avatar-com-status ${a.premium?'premium':''}"><img src="${a.avatar}"><span class="bolinha-status ${a.online?'online':''}"></span></div>
            <div class="info-amigo"><div class="nome-amigo">${escaparHtml(a.usuario)}</div><div class="sub-amigo">${a.online?'Online':'Offline'}</div></div>
            <div class="acoes-item-amigo">
                <button class="botao-mini-circulo" onclick="event.stopPropagation();abrirDM('${escaparHtml(a.usuario)}')" title="Mensagem">&#128172;</button>
                <button class="botao-mini-circulo recusar" onclick="event.stopPropagation();removerAmizade('${escaparHtml(a.usuario)}')" title="Remover">&times;</button>
            </div></div>`).join('');
}

async function enviarPedidoAmizade() {
    const campo = document.getElementById('campoAddAmigo');
    const msg = document.getElementById('msgAddAmigo');
    const alvo = campo.value.trim();
    if (!alvo) { msg.className='mensagem-modal erro'; msg.textContent = 'Digite um apelido ou #ID.'; return; }
    const r = await fetch('/api/amigos/adicionar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ alvo }) });
    const d = await r.json();
    if (d.ok) { msg.className='mensagem-modal'; msg.textContent = 'Pedido de amizade enviado!'; campo.value = ''; }
    else { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Nao foi possivel enviar.'; }
}
async function responderPedido(usuario, aceitar) {
    await fetch('/api/amigos/responder', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario, aceitar }) });
    renderizarPainelAmigosCentral();
}
async function cancelarPedidoEnviado(usuario) {
    await fetch('/api/amigos/cancelar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario }) });
    renderizarPainelAmigosCentral();
}
async function removerAmizade(usuario) {
    if (!confirm('Remover ' + usuario + ' da sua lista de amigos?')) return;
    await fetch('/api/amigos/remover', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario }) });
    renderizarPainelAmigosCentral();
}
async function desbloquearUsuario(usuario) {
    await fetch('/api/amigos/desbloquear', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario }) });
    renderizarPainelAmigosCentral();
}

// ---------------------------------------------------------------
// Mensagens diretas (DM)
// ---------------------------------------------------------------
async function carregarMensagensDM() {
    if (!estado.dmAtual) return;
    const r = await fetch('/api/dm/' + encodeURIComponent(estado.dmAtual) + '/mensagens');
    const d = await r.json();
    const div = document.getElementById('listaMensagensDM');
    if (!div) return;
    if (!d.mensagens.length) { div.innerHTML = '<div class="vazio-mensagens">Esse e o comeco da sua conversa com ' + escaparHtml(estado.dmAtual) + '.</div>'; }
    else { div.innerHTML = d.mensagens.map(m => renderizarGrupoMensagem(m, 'dm', estado.dmAtual)).join(''); div.scrollTop = div.scrollHeight; }
    if (d.mensagens.length) marcarLido('dm', estado.dmAtual, d.mensagens[d.mensagens.length-1].id);
}
function renderizarGrupoMensagem(m, tipo, alvo) {
    const hora = new Date(m.criado_em).toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit'});
    const podeEditar = m.minha;
    const podeExcluir = m.minha || m.pode_gerenciar;
    const listaEmojis = tipo === 'canal' ? (window._emojisServidorCache || []) : [];
    const reacoesHtml = (m.reacoes || []).map(rr => `<span class="pilula-reacao ${rr.reagi?'minha':''}" onclick="reagirMensagem('${tipo}', ${m.id}, '${rr.emoji}')">${rr.emoji} ${rr.qtd}</span>`).join('');
    const seletorRapido = EMOJIS_REACAO.map(e => `<span onclick="reagirMensagem('${tipo}', ${m.id}, '${e}'); fecharSeletorReacao(${m.id})">${e}</span>`).join('');
    return `<div class="grupo-mensagem" id="msg-${tipo}-${m.id}">
        <img class="avatar-msg" src="${m.avatar}" onclick="abrirPerfilDe('${escaparHtml(m.remetente)}')">
        <div class="conteudo-msg-grupo">
            <div class="cabecalho-msg">
                <span class="autor-msg" onclick="abrirPerfilDe('${escaparHtml(m.remetente)}')">${escaparHtml(m.nome_exibicao || m.remetente)}</span>
                <span class="hora-msg">${hora}</span>
                ${m.editado_em ? '<span class="editado-msg">(editada)</span>' : ''}
                ${m.fixada ? '<span class="pin-msg-tag">&#128204; fixada</span>' : ''}
            </div>
            <div class="texto-msg" id="texto-${tipo}-${m.id}">${aplicarEmojisTexto(m.conteudo, listaEmojis)}</div>
            <div class="faixa-reacoes" id="reacoes-${tipo}-${m.id}">${reacoesHtml}</div>
        </div>
        <div class="seletor-reacao-rapida" id="seletor-${tipo}-${m.id}">${seletorRapido}</div>
        <div class="acoes-mensagem">
            <button onclick="abrirSeletorReacao('${tipo}', ${m.id})" title="Reagir">&#128512;</button>
            ${podeEditar ? '<button onclick="iniciarEdicaoMensagem(\''+tipo+'\', '+m.id+')" title="Editar">&#9998;</button>' : ''}
            ${tipo === 'canal' && m.pode_gerenciar !== undefined ? '<button onclick="alternarFixarMensagem('+m.id+', '+(m.fixada?'false':'true')+')" title="Fixar">&#128204;</button>' : ''}
            ${podeExcluir ? '<button onclick="excluirMensagem(\''+tipo+'\', '+m.id+', '+(alvo?"'"+escaparHtml(String(alvo))+"'":'null')+')" title="Excluir">&#128465;</button>' : ''}
        </div>
      </div>`;
}
function abrirSeletorReacao(tipo, id) {
    document.querySelectorAll('.seletor-reacao-rapida.aberto').forEach(el => el.classList.remove('aberto'));
    document.getElementById('seletor-' + tipo + '-' + id).classList.add('aberto');
}
function fecharSeletorReacao(id) { document.querySelectorAll('.seletor-reacao-rapida.aberto').forEach(el => el.classList.remove('aberto')); }
document.addEventListener('click', (ev) => {
    if (!ev.target.closest('.seletor-reacao-rapida') && !ev.target.closest('.acoes-mensagem')) {
        document.querySelectorAll('.seletor-reacao-rapida.aberto').forEach(el => el.classList.remove('aberto'));
    }
});
async function reagirMensagem(tipo, id, emoji) {
    await fetch('/api/mensagens/reagir', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ tipo, mensagem_id: id, emoji }) });
    if (tipo === 'canal') carregarMensagensCanal(); else carregarMensagensDM();
}
function iniciarEdicaoMensagem(tipo, id) {
    const div = document.getElementById('texto-' + tipo + '-' + id);
    const textoAtual = div.textContent;
    div.innerHTML = `<div class="editando-mensagem-caixa"><input type="text" id="edicaoInput-${tipo}-${id}" value="${escaparHtml(textoAtual)}"><button onclick="confirmarEdicaoMensagem('${tipo}', ${id})">Salvar</button><button onclick="tipo==='canal'?carregarMensagensCanal():carregarMensagensDM()">Cancelar</button></div>`;
    document.getElementById(`edicaoInput-${tipo}-${id}`).focus();
}
async function confirmarEdicaoMensagem(tipo, id) {
    const novoTexto = document.getElementById(`edicaoInput-${tipo}-${id}`).value.trim();
    if (!novoTexto) return;
    const rota = tipo === 'canal' ? '/api/canais/mensagens/' + id + '/editar' : '/api/dm/mensagens/' + id + '/editar';
    await fetch(rota, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ texto: novoTexto }) });
    if (tipo === 'canal') carregarMensagensCanal(); else carregarMensagensDM();
}
async function excluirMensagem(tipo, id) {
    if (!confirm('Excluir esta mensagem?')) return;
    const rota = tipo === 'canal' ? '/api/canais/mensagens/' + id + '/excluir' : '/api/dm/mensagens/' + id + '/excluir';
    await fetch(rota, { method:'POST' });
    if (tipo === 'canal') carregarMensagensCanal(); else carregarMensagensDM();
}
async function alternarFixarMensagem(id, fixar) {
    await fetch('/api/canais/mensagens/' + id + '/fixar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ fixar }) });
    carregarMensagensCanal();
}
async function enviarMensagemDM() {
    const campo = document.getElementById('campoMensagemDM');
    const texto = campo.value.trim();
    if (!texto || !estado.dmAtual) return;
    campo.value = '';
    const r = await fetch('/api/dm/' + encodeURIComponent(estado.dmAtual) + '/enviar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ texto }) });
    const d = await r.json();
    if (!d.ok) alert(d.erro || 'Nao foi possivel enviar.');
    carregarMensagensDM();
}

// ---------------------------------------------------------------
// Mensagens de canal (servidor)
// ---------------------------------------------------------------
async function carregarMensagensCanal() {
    if (!estado.canalAtual) return;
    const r = await fetch('/api/canais/' + estado.canalAtual + '/mensagens');
    const d = await r.json();
    if (!d.ok) return;
    window._emojisServidorCache = d.emojis || [];
    const div = document.getElementById('listaMensagensCanal');
    if (!div) return;
    if (!d.mensagens.length) { div.innerHTML = '<div class="vazio-mensagens">Esse e o comeco do canal.</div>'; }
    else { div.innerHTML = d.mensagens.map(m => renderizarGrupoMensagem(m, 'canal', estado.canalAtual)).join(''); div.scrollTop = div.scrollHeight; }
    if (d.mensagens.length) marcarLido('canal', estado.canalAtual, d.mensagens[d.mensagens.length-1].id);
}
async function enviarMensagemCanal() {
    const campo = document.getElementById('campoMensagemCanal');
    const texto = campo.value.trim();
    if (!texto || !estado.canalAtual) return;
    campo.value = '';
    const r = await fetch('/api/canais/' + estado.canalAtual + '/enviar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ texto }) });
    const d = await r.json();
    if (!d.ok) alert(d.erro || 'Nao foi possivel enviar.');
    carregarMensagensCanal();
}
async function abrirFixadas() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    if (!estado.canalAtual || estado.tipoCanalAtual !== 'texto') { alert('Abra um canal de texto primeiro.'); return; }
    const r = await fetch('/api/canais/' + estado.canalAtual + '/fixadas');
    const d = await r.json();
    document.getElementById('listaFixadas').innerHTML = (d.mensagens || []).map(m => `
        <div class="linha-lista-modal"><img src="${m.avatar}"><div class="info-linha"><div class="nome-linha">${escaparHtml(m.remetente)}</div><div class="sub-linha">${escaparHtml(m.conteudo)}</div></div>
        <button onclick="alternarFixarMensagem(${m.id}, false); fecharModal('modalFixadas')">Desafixar</button></div>`).join('') || '<div class="vazio-lista-lateral">Nenhuma mensagem fixada.</div>';
    abrirModal('modalFixadas');
}

// ---------------------------------------------------------------
// Indicador de "digitando..."
// ---------------------------------------------------------------
let ultimoAvisoDigitando = 0;
async function avisarDigitando(tipo, alvo) {
    const agora = Date.now();
    if (agora - ultimoAvisoDigitando < 2500) return;
    ultimoAvisoDigitando = agora;
    fetch('/api/digitando', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ tipo, alvo: String(alvo) }) });
}
async function atualizarIndicadorDigitando(tipo, alvo, idElemento) {
    const el = document.getElementById(idElemento);
    if (!el) return;
    const r = await fetch('/api/digitando?tipo=' + tipo + '&alvo=' + encodeURIComponent(alvo));
    const nomes = await r.json();
    if (!nomes.length) { el.textContent = ''; return; }
    el.textContent = (nomes.length === 1 ? nomes[0] + ' esta digitando...' : nomes.join(', ') + ' estao digitando...');
}

// ---------------------------------------------------------------
// Nao lidos
// ---------------------------------------------------------------
async function marcarLido(tipo, alvo, ultimoId) {
    await fetch('/api/leitura/marcar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ tipo, alvo: String(alvo), ultimo_id: ultimoId }) });
}

// ---------------------------------------------------------------
// Painel de membros do servidor
// ---------------------------------------------------------------
function alternarPainelMembros() {
    document.getElementById('painelMembros').classList.toggle('aberto');
    if (document.getElementById('painelMembros').classList.contains('aberto')) renderizarPainelMembrosServidor();
}
function fecharPainelMembros() { document.getElementById('painelMembros').classList.remove('aberto'); }
async function renderizarPainelMembrosServidor() {
    const painel = document.getElementById('painelMembros');
    if (!painel.classList.contains('aberto') || !estado.servidorAtual) return;
    const servidor = await (await fetch('/api/servidores/' + estado.servidorAtual)).json();
    if (!servidor.ok) return;
    let html = `<div class="titulo-membros">MEMBROS - ${servidor.membros.length}</div>`;
    servidor.membros.forEach(m => {
        const badges = (m.cargos || []).map(c => `<span class="badge-cargo-membro" style="background:${c.cor}">${escaparHtml(c.nome)}</span>`).join('');
        html += `<div class="linha-membro-servidor" onclick="abrirPerfilDe('${escaparHtml(m.usuario)}')">
            <div class="avatar-com-status ${m.premium?'premium':''}"><img src="${m.avatar}"><span class="bolinha-status ${m.online?'online':''}"></span></div>
            <span class="nome-membro-serv">${escaparHtml(m.apelido || m.usuario)}${badges}</span></div>`;
    });
    painel.innerHTML = html;
}

// ---------------------------------------------------------------
// Apelido no servidor
// ---------------------------------------------------------------
function abrirModalApelido() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    const meuMembro = (estado.detalheServidorCache.membros || []).find(m => m.usuario === MEU_USUARIO);
    document.getElementById('campoApelidoServidor').value = (meuMembro && meuMembro.apelido) || '';
    document.getElementById('msgApelidoServidor').textContent = '';
    abrirModal('modalApelidoServidor');
}
async function salvarApelidoServidor() {
    const apelido = document.getElementById('campoApelidoServidor').value.trim();
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/apelido', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ apelido }) });
    const d = await r.json();
    if (d.ok) { fecharModal('modalApelidoServidor'); montarAreaPrincipal(); renderizarPainelMembrosServidor(); }
    else { document.getElementById('msgApelidoServidor').className = 'mensagem-modal erro'; document.getElementById('msgApelidoServidor').textContent = d.erro || 'Erro.'; }
}

// ---------------------------------------------------------------
// Servidores: criar / entrar / configurar / excluir / sair / descobrir
// ---------------------------------------------------------------
async function criarServidor() {
    const nome = document.getElementById('nomeCriarServidor').value.trim();
    const msg = document.getElementById('msgCriarServidor');
    if (!nome) { msg.className='mensagem-modal erro'; msg.textContent = 'Digite um nome.'; return; }
    const form = new FormData();
    form.append('nome', nome);
    const arquivo = document.getElementById('iconeCriarServidor').files[0];
    if (arquivo) form.append('icone', arquivo);
    const r = await fetch('/api/servidores/criar', { method:'POST', body: form });
    const d = await r.json();
    if (!d.ok) { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; return; }
    fecharModal('modalCriarServidor');
    document.getElementById('nomeCriarServidor').value = '';
    await abrirServidor(d.servidor_id);
}
async function entrarServidor() {
    const codigo = document.getElementById('codigoEntrarServidor').value.trim();
    const msg = document.getElementById('msgEntrarServidor');
    if (!codigo) { msg.className='mensagem-modal erro'; msg.textContent = 'Cole um codigo.'; return; }
    const r = await fetch('/api/servidores/entrar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ codigo }) });
    const d = await r.json();
    if (!d.ok) { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Codigo invalido.'; return; }
    fecharModal('modalEntrarServidor');
    document.getElementById('codigoEntrarServidor').value = '';
    await abrirServidor(d.servidor_id);
}
async function abrirDescoberta() {
    const r = await fetch('/api/descobrir');
    const servidores = await r.json();
    document.getElementById('listaDescoberta').innerHTML = servidores.map(s => `
        <div class="card-descoberta">
            <img src="${s.icone || ''}">
            <div class="info-descoberta"><div class="nome-descoberta">${escaparHtml(s.nome)} ${s.verificado?'&#9989;':''}</div><div class="sub-descoberta">${s.membros} membros ${s.descricao ? '- ' + escaparHtml(s.descricao) : ''}</div></div>
            <button class="botao-mini-circulo" style="width:auto; padding:0 12px; border-radius:6px;" onclick="entrarServidorPublico(${s.id})">Entrar</button>
        </div>`).join('') || '<div class="vazio-lista-lateral">Nenhum servidor publico no momento.</div>';
    abrirModal('modalDescobrir');
}
async function entrarServidorPublico(servidorId) {
    const r = await fetch('/api/descobrir/entrar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ servidor_id: servidorId }) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || 'Nao foi possivel entrar.'); return; }
    fecharModal('modalDescobrir');
    await abrirServidor(servidorId);
}
function mostrarConvite() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    const servidor = estado.detalheServidorCache;
    if (!servidor) return;
    document.getElementById('codigoConviteTexto').textContent = servidor.codigo_convite;
    abrirModal('modalConviteServidor');
}
function copiarCodigoConvite() {
    const texto = document.getElementById('codigoConviteTexto').textContent;
    navigator.clipboard.writeText(texto).then(() => alert('Codigo copiado!'));
}
function mudarAbaConfigServidor(nome, botao) {
    document.querySelectorAll('.abas-modal-topo button').forEach(b => b.classList.remove('ativa'));
    document.querySelectorAll('.secao-modal-tab').forEach(s => s.classList.remove('ativa'));
    botao.classList.add('ativa');
    document.getElementById('tabConfig' + nome.charAt(0).toUpperCase() + nome.slice(1)).classList.add('ativa');
    if (nome === 'emojis') carregarEmojisConfig();
    if (nome === 'membros') carregarMembrosConfig();
    if (nome === 'banidos') carregarBanidosConfig();
}
function abrirConfigServidor() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    const servidor = estado.detalheServidorCache;
    if (!servidor) return;
    document.getElementById('editarNomeServidor').value = servidor.nome;
    document.getElementById('editarDescricaoServidor').value = servidor.descricao || '';
    document.getElementById('editarPublicoServidor').checked = !!servidor.publico;
    document.getElementById('msgConfigServidor').textContent = '';
    document.querySelectorAll('.abas-modal-topo button')[0].click();
    abrirModal('modalConfigServidor');
}
async function salvarConfigServidor() {
    const msg = document.getElementById('msgConfigServidor');
    msg.className = 'mensagem-modal'; msg.textContent = 'Salvando...';
    const form = new FormData();
    form.append('nome', document.getElementById('editarNomeServidor').value.trim());
    form.append('descricao', document.getElementById('editarDescricaoServidor').value.trim());
    form.append('publico', document.getElementById('editarPublicoServidor').checked ? '1' : '0');
    const arquivo = document.getElementById('editarIconeServidor').files[0];
    if (arquivo) form.append('icone', arquivo);
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/editar', { method:'POST', body: form });
    const d = await r.json();
    if (d.ok) { msg.textContent = 'Salvo!'; setTimeout(() => { fecharModal('modalConfigServidor'); montarTudo(); }, 500); }
    else { msg.className = 'mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; }
}
async function carregarEmojisConfig() {
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/emojis');
    const d = await r.json();
    document.getElementById('infoSlotEmoji').textContent = 'Emojis usados: ' + d.emojis.length + ' / ' + d.limite + (d.impulsionado ? ' (servidor impulsionado)' : '');
    document.getElementById('gradeEmojisServidor').innerHTML = d.emojis.map(e => `
        <div class="emoji-item-modal"><img src="${e.url}"><span>:${escaparHtml(e.nome)}:</span><button onclick="excluirEmojiServidor(${e.id})">Excluir</button></div>`).join('') || '<div class="vazio-lista-lateral" style="width:100%;">Nenhum emoji customizado ainda.</div>';
}
async function criarEmojiServidor() {
    const nome = document.getElementById('nomeNovoEmoji').value.trim().replace(/[^a-zA-Z0-9_]/g, '');
    const arquivo = document.getElementById('arquivoNovoEmoji').files[0];
    const msg = document.getElementById('msgEmojiServidor');
    if (!nome || !arquivo) { msg.className='mensagem-modal erro'; msg.textContent = 'Preencha o nome e escolha uma imagem.'; return; }
    const form = new FormData();
    form.append('nome', nome); form.append('arquivo', arquivo);
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/emojis/criar', { method:'POST', body: form });
    const d = await r.json();
    if (!d.ok) { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; return; }
    document.getElementById('nomeNovoEmoji').value = ''; document.getElementById('arquivoNovoEmoji').value = '';
    msg.className='mensagem-modal'; msg.textContent = 'Emoji adicionado!';
    carregarEmojisConfig();
}
async function excluirEmojiServidor(id) {
    await fetch('/api/servidores/' + estado.servidorAtual + '/emojis/' + id + '/excluir', { method:'POST' });
    carregarEmojisConfig();
}
async function carregarMembrosConfig() {
    const servidor = await (await fetch('/api/servidores/' + estado.servidorAtual)).json();
    document.getElementById('listaMembrosConfig').innerHTML = servidor.membros.map(m => `
        <div class="linha-lista-modal"><img src="${m.avatar}"><div class="info-linha"><div class="nome-linha">${escaparHtml(m.apelido || m.usuario)}</div><div class="sub-linha">${m.online?'Online':'Offline'}</div></div>
        ${m.usuario !== MEU_USUARIO ? '<button class="perigo-toggle" onclick="removerMembroServidor(\''+escaparHtml(m.usuario)+'\')">Expulsar</button><button class="perigo-toggle" onclick="banirMembroServidor(\''+escaparHtml(m.usuario)+'\')">Banir</button>' : ''}</div>`).join('');
}
async function removerMembroServidor(usuario) {
    if (!confirm('Expulsar ' + usuario + ' do servidor?')) return;
    await fetch('/api/servidores/' + estado.servidorAtual + '/membro/remover', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario }) });
    carregarMembrosConfig();
}
async function banirMembroServidor(usuario) {
    if (!confirm('Banir ' + usuario + ' deste servidor? Essa pessoa nao vai conseguir voltar a entrar.')) return;
    await fetch('/api/servidores/' + estado.servidorAtual + '/membro/banir', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario }) });
    carregarMembrosConfig(); carregarBanidosConfig();
}
async function carregarBanidosConfig() {
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/banidos');
    const banidos = await r.json();
    document.getElementById('listaBanidosConfig').innerHTML = banidos.map(b => `
        <div class="linha-lista-modal"><img src="${b.avatar}"><div class="info-linha"><div class="nome-linha">${escaparHtml(b.usuario)}</div></div>
        <button class="ativo-toggle" onclick="desbanirMembroServidor('${escaparHtml(b.usuario)}')">Desbanir</button></div>`).join('') || '<div class="vazio-lista-lateral">Ninguem banido.</div>';
}
async function desbanirMembroServidor(usuario) {
    await fetch('/api/servidores/' + estado.servidorAtual + '/membro/desbanir', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario }) });
    carregarBanidosConfig();
}
async function excluirServidorAtual() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    if (!confirm('Excluir este servidor para sempre? Essa acao nao pode ser desfeita.')) return;
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/excluir', { method:'POST' });
    const d = await r.json();
    if (d.ok) { abrirVisaoAmigos(); } else { alert(d.erro || 'Nao foi possivel excluir.'); }
}
async function sairDoServidorAtual() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    if (!confirm('Sair deste servidor?')) return;
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/sair', { method:'POST' });
    const d = await r.json();
    if (d.ok) { abrirVisaoAmigos(); } else { alert(d.erro || 'Nao foi possivel sair.'); }
}

// ---------------------------------------------------------------
// Cargos
// ---------------------------------------------------------------
async function abrirModalCargos() {
    document.getElementById('menuFlutuanteServidor').classList.remove('aberto');
    abrirModal('modalCargosServidor');
    await carregarListaCargos();
}
async function carregarListaCargos() {
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/cargos');
    const cargos = await r.json();
    const div = document.getElementById('listaCargosModal');
    div.innerHTML = cargos.map(c => `<div style="display:flex; align-items:center; gap:8px; padding:8px 0; border-bottom:1px solid #3a3c41;">
        <span style="width:10px; height:10px; border-radius:50%; background:${c.cor}; flex-shrink:0;"></span>
        <span style="flex:1; color:#dbdee1; font-size:13px;">${escaparHtml(c.nome)}</span>
        <button class="botao-mini-circulo" style="width:auto; padding:0 10px; border-radius:6px;" onclick="atribuirCargoPrompt(${c.id}, '${escaparHtml(c.nome)}')">Atribuir</button>
        <button class="botao-mini-circulo recusar" style="width:auto; padding:0 10px; border-radius:6px;" onclick="excluirCargo(${c.id})">Excluir</button>
        </div>`).join('') || '<div class="vazio-lista-lateral">Nenhum cargo criado ainda.</div>';
}
async function criarCargo() {
    const nome = document.getElementById('nomeNovoCargo').value.trim();
    const cor = document.getElementById('corNovoCargo').value;
    const msg = document.getElementById('msgCargosServidor');
    if (!nome) { msg.className='mensagem-modal erro'; msg.textContent = 'Digite um nome.'; return; }
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/cargos/criar', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ nome, cor }) });
    const d = await r.json();
    if (d.ok) { document.getElementById('nomeNovoCargo').value=''; msg.className='mensagem-modal'; msg.textContent='Cargo criado!'; carregarListaCargos(); }
    else { msg.className='mensagem-modal erro'; msg.textContent = d.erro || 'Erro.'; }
}
async function excluirCargo(id) {
    if (!confirm('Excluir este cargo?')) return;
    await fetch('/api/servidores/' + estado.servidorAtual + '/cargos/' + id + '/excluir', { method:'POST' });
    carregarListaCargos();
}
async function atribuirCargoPrompt(cargoId, nomeCargo) {
    const usuario = prompt('Apelido de quem vai receber o cargo "' + nomeCargo + '":');
    if (!usuario) return;
    const r = await fetch('/api/servidores/' + estado.servidorAtual + '/cargos/atribuir', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ usuario: usuario.trim(), cargo_id: cargoId }) });
    const d = await r.json();
    if (!d.ok) alert(d.erro || 'Nao foi possivel atribuir.');
    else renderizarPainelMembrosServidor();
}

// ---------------------------------------------------------------
// Painel do administrador (sem pagamento - liberacao manual)
// ---------------------------------------------------------------
async function abrirPainelAdmin() {
    abrirModal('modalAdmin');
    await renderizarAdminUsuarios();
}
function mudarAbaAdmin(nome, botao) {
    document.querySelectorAll('#modalAdmin .abas-modal-topo button').forEach(b => b.classList.remove('ativa'));
    document.querySelectorAll('#modalAdmin .secao-modal-tab').forEach(s => s.classList.remove('ativa'));
    botao.classList.add('ativa');
    document.getElementById('tabAdmin' + nome.charAt(0).toUpperCase() + nome.slice(1)).classList.add('ativa');
    if (nome === 'usuarios') renderizarAdminUsuarios(); else renderizarAdminServidores();
}
async function renderizarAdminUsuarios() {
    const termo = (document.getElementById('buscaAdminUsuarios').value || '').toLowerCase();
    const r = await fetch('/api/admin/usuarios');
    const usuarios = await r.json();
    const filtrados = usuarios.filter(u => u.usuario.toLowerCase().includes(termo));
    document.getElementById('listaAdminUsuarios').innerHTML = filtrados.map(u => `
        <div class="linha-lista-modal">
            <img src="${u.avatar}">
            <div class="info-linha"><div class="nome-linha">${escaparHtml(u.usuario)} #${u.id_publico}</div><div class="sub-linha">${u.eh_admin?'Administrador':(u.online?'Online':'Offline')}</div></div>
            <button class="${u.premium?'ativo-toggle':''}" onclick="alternarPremiumUsuario('${escaparHtml(u.usuario)}', ${!u.premium})">${u.premium?'Recurso liberado':'Liberar recurso extra'}</button>
            <button class="perigo-toggle" onclick="alternarBanUsuario('${escaparHtml(u.usuario)}', ${!u.banido})" ${u.eh_admin?'disabled':''}>${u.banido?'Desbanir':'Banir'}</button>
        </div>`).join('') || '<div class="vazio-lista-lateral">Nenhum usuario encontrado.</div>';
}
async function alternarPremiumUsuario(usuario, conceder) {
    await fetch('/api/admin/usuarios/premium', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ alvo: usuario, conceder }) });
    renderizarAdminUsuarios();
}
async function alternarBanUsuario(usuario, banir) {
    if (banir && !confirm('Banir ' + usuario + ' do NOVO GG inteiro?')) return;
    await fetch('/api/admin/usuarios/banir', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ alvo: usuario, banir }) });
    renderizarAdminUsuarios();
}
async function renderizarAdminServidores() {
    const termo = (document.getElementById('buscaAdminServidores').value || '').toLowerCase();
    const r = await fetch('/api/admin/servidores');
    const servidores = await r.json();
    const filtrados = servidores.filter(s => s.nome.toLowerCase().includes(termo));
    document.getElementById('listaAdminServidores').innerHTML = filtrados.map(s => `
        <div class="linha-lista-modal">
            <img src="${s.icone || ''}">
            <div class="info-linha"><div class="nome-linha">${escaparHtml(s.nome)}</div><div class="sub-linha">dono: ${escaparHtml(s.dono)} - ${s.membros} membros</div></div>
            <button class="${s.verificado?'ativo-toggle':''}" onclick="alternarVerificarServidorAdmin(${s.id}, ${!s.verificado})">Verificado</button>
            <button class="${s.impulsionado?'ativo-toggle':''}" onclick="alternarImpulsionarServidorAdmin(${s.id}, ${!s.impulsionado})">Impulsionado</button>
            <button class="perigo-toggle" onclick="excluirServidorAdmin(${s.id})">Excluir</button>
        </div>`).join('') || '<div class="vazio-lista-lateral">Nenhum servidor encontrado.</div>';
}
async function alternarVerificarServidorAdmin(id, verificar) {
    await fetch('/api/admin/servidores/verificar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ servidor_id: id, verificar }) });
    renderizarAdminServidores();
}
async function alternarImpulsionarServidorAdmin(id, impulsionar) {
    await fetch('/api/admin/servidores/impulsionar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ servidor_id: id, impulsionar }) });
    renderizarAdminServidores();
}
async function excluirServidorAdmin(id) {
    if (!confirm('Excluir este servidor (acao de administrador)?')) return;
    await fetch('/api/admin/servidores/' + id + '/excluir', { method:'POST' });
    renderizarAdminServidores(); carregarRailServidores();
}

// ---------------------------------------------------------------
// Canal de voz do servidor: mesh WebRTC com sinalizacao por polling
// ---------------------------------------------------------------
let vozConexoes = {};
let vozStreamLocal = null;
let vozTelaStream = null;
let vozCanalAtualId = null;
let vozPollSinais = null;
let vozMutado = false;
let vozSurdo = false;
let vozCompartilhandoTela = false;
let usuariosFalando = new Set();
let vozAnalisadores = {};

function monitorarVolumeVoz(stream, usuario) {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const origem = ctx.createMediaStreamSource(stream);
        const analisador = ctx.createAnalyser();
        analisador.fftSize = 512;
        origem.connect(analisador);
        const dados = new Uint8Array(analisador.frequencyBinCount);
        const intervalo = setInterval(() => {
            analisador.getByteFrequencyData(dados);
            const media = dados.reduce((a,b) => a+b, 0) / dados.length;
            const falando = media > 12;
            if (falando && !usuariosFalando.has(usuario)) { usuariosFalando.add(usuario); atualizarClasseFalandoVoz(); }
            else if (!falando && usuariosFalando.has(usuario)) { usuariosFalando.delete(usuario); atualizarClasseFalandoVoz(); }
        }, 200);
        vozAnalisadores[usuario] = { ctx, intervalo };
    } catch (e) {}
}
function pararMonitorVolumeVoz(usuario) {
    const m = vozAnalisadores[usuario];
    if (m) { clearInterval(m.intervalo); try { m.ctx.close(); } catch(e){} delete vozAnalisadores[usuario]; }
    usuariosFalando.delete(usuario);
}
function atualizarClasseFalandoVoz() {
    document.querySelectorAll('.card-voz-participante').forEach(el => { el.classList.toggle('falando', usuariosFalando.has(el.dataset.usuario)); });
}
async function criarConexaoVoz(outroUsuario, souIniciador, canalId) {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    vozStreamLocal.getTracks().forEach(t => pc.addTrack(t, vozStreamLocal));
    pc.ontrack = (ev) => {
        if (ev.track.kind === 'video') {
            let videoEl = document.getElementById('video-voz-' + outroUsuario);
            if (!videoEl) {
                const card = document.querySelector('.card-voz-participante[data-usuario="' + outroUsuario + '"]');
                videoEl = document.createElement('video');
                videoEl.id = 'video-voz-' + outroUsuario; videoEl.autoplay = true; videoEl.playsInline = true;
                if (card) card.insertBefore(videoEl, card.firstChild);
            }
            videoEl.srcObject = ev.streams[0];
            return;
        }
        let audioEl = document.getElementById('audio-voz-' + outroUsuario);
        if (!audioEl) { audioEl = document.createElement('audio'); audioEl.id = 'audio-voz-' + outroUsuario; audioEl.autoplay = true; audioEl.muted = vozSurdo; document.body.appendChild(audioEl); }
        audioEl.srcObject = ev.streams[0];
        monitorarVolumeVoz(ev.streams[0], outroUsuario);
    };
    pc.onicecandidate = (ev) => { if (ev.candidate) enviarSinalVoz(canalId, outroUsuario, 'candidato', ev.candidate); };
    vozConexoes[outroUsuario] = pc;
    if (souIniciador) {
        const oferta = await pc.createOffer();
        await pc.setLocalDescription(oferta);
        enviarSinalVoz(canalId, outroUsuario, 'oferta', oferta);
    }
    return pc;
}
async function enviarSinalVoz(canalId, para, tipo, dados) {
    await fetch('/api/voz/sinal', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ canal_id: canalId, para, tipo, dados }) });
}
async function pollarSinaisVoz() {
    if (!vozCanalAtualId) return;
    const r = await fetch('/api/voz/sinais?canal_id=' + vozCanalAtualId);
    const sinais = await r.json();
    for (const s of sinais) {
        let pc = vozConexoes[s.de];
        if (s.tipo === 'oferta') {
            if (!pc) pc = await criarConexaoVoz(s.de, false, vozCanalAtualId);
            await pc.setRemoteDescription(s.dados);
            const resposta = await pc.createAnswer();
            await pc.setLocalDescription(resposta);
            enviarSinalVoz(vozCanalAtualId, s.de, 'resposta', resposta);
        } else if (s.tipo === 'resposta') {
            if (pc) await pc.setRemoteDescription(s.dados);
        } else if (s.tipo === 'candidato') {
            if (pc) { try { await pc.addIceCandidate(s.dados); } catch(e) {} }
        }
    }
}
function htmlBotoesVoz() {
    return `<button class="${vozMutado?'ativado':''}" onclick="alternarMudoVoz()" id="botaoMudoVoz" title="Mutar/desmutar">${vozMutado?'&#128263;':'&#127908;'}</button>
            <button class="${vozSurdo?'ativado':''}" onclick="alternarSurdoVoz()" id="botaoSurdoVoz" title="Ensurdecer">&#128266;</button>
            <button class="${vozCompartilhandoTela?'ativado':''}" onclick="alternarCompartilharTelaVoz()" id="botaoTelaVoz" title="Compartilhar tela">&#128421;</button>
            <button class="sair" onclick="sairCanalVoz()" title="Sair">&#9632;</button>`;
}
async function entrarCanalVoz(canalId) {
    try { vozStreamLocal = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { alert('Nao foi possivel acessar o microfone.'); return; }
    vozCanalAtualId = canalId;
    monitorarVolumeVoz(vozStreamLocal, MEU_USUARIO);
    await fetch('/api/voz/entrar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ canal_id: canalId }) });
    const r = await fetch('/api/voz/participantes?canal_id=' + canalId);
    const participantes = await r.json();
    for (const p of participantes) { if (p.usuario !== MEU_USUARIO) await criarConexaoVoz(p.usuario, MEU_USUARIO < p.usuario, canalId); }
    const bcv = document.getElementById('botoesControleVoz');
    if (bcv) bcv.innerHTML = htmlBotoesVoz();
    vozPollSinais = setInterval(pollarSinaisVoz, 1500);
    atualizarParticipantesVoz();
}
function alternarMudoVoz() {
    if (!vozStreamLocal) return;
    vozMutado = !vozMutado;
    vozStreamLocal.getAudioTracks().forEach(t => t.enabled = !vozMutado);
    const bcv = document.getElementById('botoesControleVoz'); if (bcv) bcv.innerHTML = htmlBotoesVoz();
}
function alternarSurdoVoz() {
    vozSurdo = !vozSurdo;
    document.querySelectorAll("audio[id^='audio-voz-']").forEach(a => a.muted = vozSurdo);
    if (vozSurdo && !vozMutado) { vozMutado = true; if (vozStreamLocal) vozStreamLocal.getAudioTracks().forEach(t => t.enabled = false); }
    const bcv = document.getElementById('botoesControleVoz'); if (bcv) bcv.innerHTML = htmlBotoesVoz();
}
async function renegociarComTodosVoz() {
    for (const usuario in vozConexoes) {
        const pc = vozConexoes[usuario];
        const oferta = await pc.createOffer();
        await pc.setLocalDescription(oferta);
        enviarSinalVoz(vozCanalAtualId, usuario, 'oferta', oferta);
    }
}
async function alternarCompartilharTelaVoz() {
    if (vozCompartilhandoTela) { pararCompartilharTelaVoz(); return; }
    try { vozTelaStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }); }
    catch (e) { return; }
    const trackTela = vozTelaStream.getVideoTracks()[0];
    for (const usuario in vozConexoes) { vozConexoes[usuario].addTrack(trackTela, vozTelaStream); }
    vozCompartilhandoTela = true;
    trackTela.onended = () => pararCompartilharTelaVoz();
    await renegociarComTodosVoz();
    const bcv = document.getElementById('botoesControleVoz'); if (bcv) bcv.innerHTML = htmlBotoesVoz();
}
function pararCompartilharTelaVoz() {
    if (vozTelaStream) { vozTelaStream.getTracks().forEach(t => {
        for (const usuario in vozConexoes) {
            const remetente = vozConexoes[usuario].getSenders().find(s => s.track === t);
            if (remetente) vozConexoes[usuario].removeTrack(remetente);
        }
        t.stop();
    }); vozTelaStream = null; }
    vozCompartilhandoTela = false;
    renegociarComTodosVoz();
    const bcv = document.getElementById('botoesControleVoz'); if (bcv) bcv.innerHTML = htmlBotoesVoz();
}
async function sairCanalVoz() {
    if (!vozCanalAtualId) return;
    const canalEncerrado = vozCanalAtualId;
    await fetch('/api/voz/sair', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ canal_id: canalEncerrado }) });
    Object.values(vozConexoes).forEach(pc => pc.close());
    Object.keys(vozAnalisadores).forEach(pararMonitorVolumeVoz);
    pararMonitorVolumeVoz(MEU_USUARIO);
    vozConexoes = {};
    if (vozStreamLocal) { vozStreamLocal.getTracks().forEach(t => t.stop()); vozStreamLocal = null; }
    if (vozTelaStream) { vozTelaStream.getTracks().forEach(t => t.stop()); vozTelaStream = null; }
    document.querySelectorAll("audio[id^='audio-voz-']").forEach(a => a.remove());
    document.querySelectorAll("video[id^='video-voz-']").forEach(v => v.remove());
    if (vozPollSinais) { clearInterval(vozPollSinais); vozPollSinais = null; }
    vozCanalAtualId = null; vozMutado = false; vozSurdo = false; vozCompartilhandoTela = false;
    const bcv = document.getElementById('botoesControleVoz');
    if (bcv) bcv.innerHTML = `<button class="entrar" onclick="entrarCanalVoz(${canalEncerrado})">&#128222;</button>`;
}
async function atualizarParticipantesVoz() {
    if (!estado.canalAtual || estado.tipoCanalAtual !== 'voz') return;
    const r = await fetch('/api/voz/participantes?canal_id=' + estado.canalAtual);
    const participantes = await r.json();
    if (vozCanalAtualId === estado.canalAtual) {
        for (const p of participantes) { if (p.usuario !== MEU_USUARIO && !vozConexoes[p.usuario]) await criarConexaoVoz(p.usuario, MEU_USUARIO < p.usuario, estado.canalAtual); }
        for (const usuario in vozConexoes) {
            if (!participantes.find(p => p.usuario === usuario)) {
                vozConexoes[usuario].close(); delete vozConexoes[usuario];
                pararMonitorVolumeVoz(usuario);
                const audioEl = document.getElementById('audio-voz-' + usuario); if (audioEl) audioEl.remove();
                const videoEl = document.getElementById('video-voz-' + usuario); if (videoEl) videoEl.remove();
            }
        }
    }
    const grade = document.getElementById('gradeVozParticipantes');
    if (grade) grade.innerHTML = participantes.map(p => `<div class="card-voz-participante" data-usuario="${escaparHtml(p.usuario)}"><img class="avatar-voz" src="${p.avatar}"><span>${escaparHtml(p.usuario)}</span></div>`).join('') || '<div class="vazio-lista-lateral">Ninguem no canal ainda.</div>';
    atualizarClasseFalandoVoz();
    const badge = document.getElementById('canalvoz-' + estado.canalAtual);
    if (badge) { const b = badge.querySelector('.contagem-voz'); if (b) b.textContent = participantes.length ? participantes.length : ''; }
}
window.addEventListener('beforeunload', () => {
    if (vozCanalAtualId) navigator.sendBeacon('/api/voz/sair', new Blob([JSON.stringify({ canal_id: vozCanalAtualId })], { type: 'application/json' }));
    if (chamadaDmAtualId) navigator.sendBeacon('/api/chamada/encerrar', new Blob([JSON.stringify({ chamada_id: chamadaDmAtualId })], { type: 'application/json' }));
});

// ---------------------------------------------------------------
// Chamadas de voz/video em DM (1 para 1)
// ---------------------------------------------------------------
let dmPc = null, dmStreamLocal = null, chamadaDmAtualId = null, contatoChamadaDm = null, dmComVideo = false;
let dmPollCandidatos = null, dmPollStatus = null, dmIndiceCandidatosRecebidos = 0, dmMutado = false;

function abrirModalChamadaDM(nome, avatar, statusTexto, botoesHtml, comVideo) {
    document.getElementById('nomeChamadaDM').textContent = nome;
    document.getElementById('avatarChamadaDM').src = avatar || '';
    document.getElementById('avatarChamadaDM').style.display = comVideo ? 'none' : '';
    document.getElementById('statusChamadaDM').textContent = statusTexto;
    document.getElementById('botoesChamadaDM').innerHTML = botoesHtml;
    document.getElementById('modalChamadaDM').classList.add('aberto');
}
function fecharModalChamadaDM() {
    document.getElementById('modalChamadaDM').classList.remove('aberto');
    document.getElementById('avatarChamadaDM').style.display = '';
    document.getElementById('videoRemotoDM').classList.remove('ativo'); document.getElementById('videoRemotoDM').srcObject = null;
    document.getElementById('videoLocalDM').classList.remove('ativo'); document.getElementById('videoLocalDM').srcObject = null;
}
async function criarConexaoDM(comVideo) {
    dmPc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    dmStreamLocal = await navigator.mediaDevices.getUserMedia({ audio: true, video: comVideo ? { facingMode: 'user' } : false });
    dmStreamLocal.getTracks().forEach(t => dmPc.addTrack(t, dmStreamLocal));
    if (comVideo) { const vl = document.getElementById('videoLocalDM'); vl.srcObject = dmStreamLocal; vl.classList.add('ativo'); }
    dmPc.ontrack = (ev) => {
        document.getElementById('audioRemotoDM').srcObject = ev.streams[0];
        if (ev.track.kind === 'video') { const vr = document.getElementById('videoRemotoDM'); vr.srcObject = ev.streams[0]; vr.classList.add('ativo'); }
    };
    dmPc.onicecandidate = (ev) => { if (ev.candidate && chamadaDmAtualId) fetch('/api/chamada/candidato', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ chamada_id: chamadaDmAtualId, candidato: ev.candidate }) }); };
    dmPc.onconnectionstatechange = () => {
        if (!dmPc) return;
        if (dmPc.connectionState === 'connected') document.getElementById('statusChamadaDM').textContent = 'Em chamada';
        else if (dmPc.connectionState === 'failed' || dmPc.connectionState === 'disconnected' || dmPc.connectionState === 'closed') encerrarChamadaDM(false);
    };
}
function botoesEmChamadaDmHtml() {
    let html = `<button class="botao-chamada-circulo neutro" id="botaoMudoDM" onclick="alternarMudoDM()">${dmMutado?'&#128263;':'&#127908;'}</button>`;
    html += `<button class="botao-chamada-circulo encerrar" onclick="encerrarChamadaDM(true)">&#128222;</button>`;
    return html;
}
function alternarMudoDM() {
    if (!dmStreamLocal) return;
    dmMutado = !dmMutado;
    dmStreamLocal.getAudioTracks().forEach(t => t.enabled = !dmMutado);
    document.getElementById('botoesChamadaDM').innerHTML = botoesEmChamadaDmHtml();
}
async function iniciarChamadaDM(comVideo) {
    if (!estado.dmAtual) return;
    contatoChamadaDm = estado.dmAtual; dmComVideo = !!comVideo;
    abrirModalChamadaDM(contatoChamadaDm, null, 'Chamando...', '<button class="botao-chamada-circulo encerrar" onclick="encerrarChamadaDM(true)">&#128222;</button>', dmComVideo);
    try { await criarConexaoDM(dmComVideo); } catch (e) { alert('Nao foi possivel acessar o microfone/camera.'); fecharModalChamadaDM(); return; }
    const oferta = await dmPc.createOffer();
    await dmPc.setLocalDescription(oferta);
    const r = await fetch('/api/chamada/iniciar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ contato: contatoChamadaDm, oferta, com_video: dmComVideo }) });
    const d = await r.json();
    if (!d.ok) { alert(d.erro || 'Nao foi possivel ligar.'); fecharModalChamadaDM(); return; }
    chamadaDmAtualId = d.chamada_id;
    iniciarPollCandidatosDM();
    dmPollStatus = setInterval(async () => {
        const rs = await fetch('/api/chamada/status/' + chamadaDmAtualId);
        const ds = await rs.json();
        if (ds.status === 'aceita' && ds.resposta && dmPc && !dmPc.currentRemoteDescription) {
            await dmPc.setRemoteDescription(ds.resposta);
            document.getElementById('statusChamadaDM').textContent = 'Em chamada';
            document.getElementById('botoesChamadaDM').innerHTML = botoesEmChamadaDmHtml();
        } else if (ds.status === 'recusada') { document.getElementById('statusChamadaDM').textContent = 'Chamada recusada'; setTimeout(() => encerrarChamadaDM(false), 1200); }
        else if (ds.status === 'encerrada') { encerrarChamadaDM(false); }
    }, 1500);
}
async function verificarChamadaDmEntrando() {
    if (chamadaDmAtualId) return;
    const r = await fetch('/api/chamada/pendente');
    const d = await r.json();
    if (!d.chamada) return;
    chamadaDmAtualId = d.chamada.id; contatoChamadaDm = d.chamada.de;
    window._ofertaRecebidaDM = d.chamada.oferta; dmComVideo = !!d.chamada.com_video;
    abrirModalChamadaDM(contatoChamadaDm, null, dmComVideo ? 'Chamada de video recebida...' : 'Chamada recebida...',
        '<button class="botao-chamada-circulo aceitar" onclick="aceitarChamadaDM()">&#9742;</button><button class="botao-chamada-circulo recusar" onclick="recusarChamadaDM()">&#10006;</button>', dmComVideo);
}
async function aceitarChamadaDM() {
    try { await criarConexaoDM(dmComVideo); } catch (e) { recusarChamadaDM(); return; }
    await dmPc.setRemoteDescription(window._ofertaRecebidaDM);
    const resposta = await dmPc.createAnswer();
    await dmPc.setLocalDescription(resposta);
    await fetch('/api/chamada/responder', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ chamada_id: chamadaDmAtualId, resposta, aceitar: true }) });
    document.getElementById('statusChamadaDM').textContent = 'Em chamada';
    document.getElementById('botoesChamadaDM').innerHTML = botoesEmChamadaDmHtml();
    iniciarPollCandidatosDM();
}
async function recusarChamadaDM() {
    await fetch('/api/chamada/responder', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ chamada_id: chamadaDmAtualId, aceitar: false }) });
    encerrarChamadaDM(false);
}
function iniciarPollCandidatosDM() {
    dmIndiceCandidatosRecebidos = 0;
    let filaPendentes = [];
    async function tentarAdicionar(c) {
        if (dmPc && dmPc.remoteDescription && dmPc.remoteDescription.type) { try { await dmPc.addIceCandidate(c); } catch(e) {} }
        else filaPendentes.push(c);
    }
    dmPollCandidatos = setInterval(async () => {
        if (!chamadaDmAtualId || !dmPc) return;
        if (dmPc.remoteDescription && dmPc.remoteDescription.type && filaPendentes.length) {
            const pendentes = filaPendentes; filaPendentes = [];
            for (const c of pendentes) { try { await dmPc.addIceCandidate(c); } catch(e) {} }
        }
        const r = await fetch('/api/chamada/candidatos/' + chamadaDmAtualId + '?desde=' + dmIndiceCandidatosRecebidos);
        const d = await r.json();
        for (const c of d.candidatos) { await tentarAdicionar(c); }
        dmIndiceCandidatosRecebidos += d.candidatos.length;
        if (d.status === 'encerrada' || d.status === 'recusada') encerrarChamadaDM(false);
    }, 1500);
}
async function encerrarChamadaDM(avisarServidor) {
    if (avisarServidor && chamadaDmAtualId) fetch('/api/chamada/encerrar', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ chamada_id: chamadaDmAtualId }) });
    if (dmPc) { dmPc.close(); dmPc = null; }
    if (dmStreamLocal) { dmStreamLocal.getTracks().forEach(t => t.stop()); dmStreamLocal = null; }
    if (dmPollCandidatos) { clearInterval(dmPollCandidatos); dmPollCandidatos = null; }
    if (dmPollStatus) { clearInterval(dmPollStatus); dmPollStatus = null; }
    chamadaDmAtualId = null; contatoChamadaDm = null; dmMutado = false;
    fecharModalChamadaDM();
}
setInterval(verificarChamadaDmEntrando, 2500);

// ---------------------------------------------------------------
// Presenca (heartbeat) e nao lidos globais + inicializacao
// ---------------------------------------------------------------
async function pulsarPresenca() { try { await fetch('/api/heartbeat', { method: 'POST' }); } catch(e) {} }
setInterval(pulsarPresenca, 20000);
setInterval(async () => { window._naoLidosCache = await (await fetch('/api/nao_lidos')).json(); if (estado.contexto === 'servidor') montarColunaLateral(); }, 8000);

document.querySelectorAll('.fundo-modal').forEach(m => {
    m.addEventListener('click', (ev) => { if (ev.target === m) m.classList.remove('aberto'); });
});

(async function inicializar() {
    pulsarPresenca();
    window._naoLidosCache = await (await fetch('/api/nao_lidos')).json();
    abrirVisaoAmigos();
    setInterval(() => { if (estado.contexto === 'amigos') renderizarPainelAmigosCentral(); }, 6000);
})();
</script>
"""


@app.route("/app")
def pagina_app():
    if not usuario_logado():
        return redirect(url_for("raiz"))
    linha = buscar_usuario(usuario_logado())
    if linha and linha["banido"]:
        session.pop("usuario", None)
        return pagina_html(NOME_APP, PAGINA_BANIDO)
    marcar_atividade(usuario_logado())
    script = (
        SCRIPT_APP
        .replace("%%ICE_SERVERS%%", ICE_SERVERS_JSON)
        .replace("%%USUARIO_JSON%%", json.dumps(usuario_logado()))
        .replace("%%EMOJIS_REACAO%%", json.dumps(EMOJIS_REACAO))
    )
    return pagina_html(NOME_APP, CORPO_APP_SHELL, ESTILO_APP, script)


# =====================================================================
# API: perfil / presenca
# =====================================================================

@app.route("/api/me")
def api_me():
    erro = exigir_login()
    if erro:
        return erro
    linha = buscar_usuario(usuario_logado())
    return jsonify({
        "usuario": linha["usuario"], "id_publico": linha["id_publico"], "avatar": avatar_de(linha),
        "status_texto": linha["status_texto"], "bio": linha["bio"], "banner": linha["banner"],
        "premium": eh_premium(linha["usuario"]), "eh_admin": bool(linha["eh_admin"]),
    })


@app.route("/api/usuarios/<nome>")
def api_usuario_detalhe(nome):
    erro = exigir_login()
    if erro:
        return erro
    linha = buscar_usuario(nome)
    if not linha:
        return jsonify({"usuario": nome, "id_publico": None, "avatar": AVATAR_PADRAO + nome})
    return jsonify({
        "usuario": linha["usuario"], "id_publico": linha["id_publico"], "avatar": avatar_de(linha),
        "status_texto": linha["status_texto"], "online": esta_online(linha["ultima_atividade"]),
        "premium": bool(linha["premium"]),
    })


@app.route("/api/usuarios/<nome>/perfil")
def api_usuario_perfil_completo(nome):
    erro = exigir_login()
    if erro:
        return erro
    linha = buscar_usuario(nome)
    if not linha:
        return jsonify({"ok": False}), 404
    return jsonify({
        "usuario": linha["usuario"], "id_publico": linha["id_publico"], "avatar": avatar_de(linha),
        "banner": linha["banner"], "bio": linha["bio"], "criado_em": linha["criado_em"],
        "online": esta_online(linha["ultima_atividade"]), "premium": bool(linha["premium"] or linha["eh_admin"]),
        "eh_admin": bool(linha["eh_admin"]),
    })


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    erro = exigir_login()
    if erro:
        return erro
    marcar_atividade(usuario_logado())
    return jsonify({"ok": True})


@app.route("/api/perfil/editar", methods=["POST"])
def api_perfil_editar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    status_texto = (request.form.get("status_texto") or "").strip()
    bio = (request.form.get("bio") or "").strip()
    conexao = obter_bd()
    arquivo = request.files.get("avatar")
    if arquivo and arquivo.filename:
        url = salvar_imagem_enviada(arquivo)
        if url:
            conexao.execute("UPDATE usuarios SET avatar = ? WHERE usuario = ? COLLATE NOCASE", (url, usuario))
    arquivo_banner = request.files.get("banner")
    if arquivo_banner and arquivo_banner.filename:
        if not eh_premium(usuario):
            conexao.close()
            return jsonify({"ok": False, "erro": "Banner de perfil e um recurso liberado pelo administrador."})
        url_banner = salvar_imagem_enviada(arquivo_banner)
        if url_banner:
            conexao.execute("UPDATE usuarios SET banner = ? WHERE usuario = ? COLLATE NOCASE", (url_banner, usuario))
    conexao.execute(
        "UPDATE usuarios SET status_texto = ?, bio = ? WHERE usuario = ? COLLATE NOCASE",
        (status_texto or None, bio or None, usuario),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: amigos
# =====================================================================

@app.route("/api/amigos")
def api_amigos_listar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT * FROM amizades WHERE status = 'aceita' AND (solicitante = ? COLLATE NOCASE OR destinatario = ? COLLATE NOCASE)",
        (usuario, usuario),
    ).fetchall()
    resultado = []
    for l in linhas:
        outro_nome = l["destinatario"] if l["solicitante"].lower() == usuario.lower() else l["solicitante"]
        outro = buscar_usuario(outro_nome)
        if not outro:
            continue
        resultado.append({
            "usuario": outro["usuario"], "id_publico": outro["id_publico"], "avatar": avatar_de(outro),
            "online": esta_online(outro["ultima_atividade"]), "premium": bool(outro["premium"] or outro["eh_admin"]),
        })
    conexao.close()
    resultado.sort(key=lambda x: (not x["online"], x["usuario"].lower()))
    return jsonify(resultado)


@app.route("/api/amigos/pendentes")
def api_amigos_pendentes():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    recebidos = conexao.execute(
        "SELECT * FROM amizades WHERE status = 'pendente' AND destinatario = ? COLLATE NOCASE", (usuario,)
    ).fetchall()
    enviados = conexao.execute(
        "SELECT * FROM amizades WHERE status = 'pendente' AND solicitante = ? COLLATE NOCASE", (usuario,)
    ).fetchall()
    conexao.close()

    def montar(lista, campo_nome_outro):
        saida = []
        for l in lista:
            outro = buscar_usuario(l[campo_nome_outro])
            if not outro:
                continue
            saida.append({"usuario": outro["usuario"], "avatar": avatar_de(outro)})
        return saida

    return jsonify({"recebidos": montar(recebidos, "solicitante"), "enviados": montar(enviados, "destinatario")})


@app.route("/api/bloqueados")
def api_bloqueados():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    linhas = conexao.execute("SELECT bloqueado FROM bloqueios WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchall()
    conexao.close()
    resultado = []
    for l in linhas:
        outro = buscar_usuario(l["bloqueado"])
        if outro:
            resultado.append({"usuario": outro["usuario"], "avatar": avatar_de(outro)})
    return jsonify(resultado)


@app.route("/api/amigos/adicionar", methods=["POST"])
def api_amigos_adicionar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    alvo_valor = (dados.get("alvo") or "").strip()
    if not alvo_valor:
        return jsonify({"ok": False, "erro": "Digite um apelido ou #ID."})
    alvo = buscar_usuario_por_nick_ou_id(alvo_valor)
    if not alvo:
        return jsonify({"ok": False, "erro": "Nao encontramos ninguem com esse apelido/ID."})
    if alvo["usuario"].lower() == usuario.lower():
        return jsonify({"ok": False, "erro": "Voce nao pode adicionar a si mesmo."})
    if usuarios_sao_bloqueados(usuario, alvo["usuario"]):
        return jsonify({"ok": False, "erro": "Nao foi possivel enviar o pedido para esse usuario."})

    conexao = obter_bd()
    ja_existe = conexao.execute(
        "SELECT * FROM amizades WHERE (solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE) "
        "OR (solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE)",
        (usuario, alvo["usuario"], alvo["usuario"], usuario),
    ).fetchone()
    if ja_existe:
        if ja_existe["status"] == "aceita":
            conexao.close()
            return jsonify({"ok": False, "erro": "Voces ja sao amigos."})
        if ja_existe["status"] == "pendente":
            if ja_existe["destinatario"].lower() == usuario.lower():
                conexao.execute(
                    "UPDATE amizades SET status = 'aceita', atualizado_em = ? WHERE id = ?",
                    (datetime.now().isoformat(), ja_existe["id"]),
                )
                conexao.commit()
                conexao.close()
                return jsonify({"ok": True, "aceito_automaticamente": True})
            conexao.close()
            return jsonify({"ok": False, "erro": "Voce ja enviou um pedido para essa pessoa."})
    conexao.execute(
        "INSERT INTO amizades (solicitante, destinatario, status, criado_em) VALUES (?, ?, 'pendente', ?)",
        (usuario, alvo["usuario"], datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/amigos/responder", methods=["POST"])
def api_amigos_responder():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    outro = (dados.get("usuario") or "").strip()
    aceitar = bool(dados.get("aceitar"))
    conexao = obter_bd()
    pedido = conexao.execute(
        "SELECT * FROM amizades WHERE status = 'pendente' AND solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE",
        (outro, usuario),
    ).fetchone()
    if not pedido:
        conexao.close()
        return jsonify({"ok": False, "erro": "Pedido nao encontrado."})
    if aceitar:
        conexao.execute("UPDATE amizades SET status = 'aceita', atualizado_em = ? WHERE id = ?", (datetime.now().isoformat(), pedido["id"]))
    else:
        conexao.execute("DELETE FROM amizades WHERE id = ?", (pedido["id"],))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/amigos/cancelar", methods=["POST"])
def api_amigos_cancelar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    outro = (dados.get("usuario") or "").strip()
    conexao = obter_bd()
    conexao.execute(
        "DELETE FROM amizades WHERE status = 'pendente' AND solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE",
        (usuario, outro),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/amigos/remover", methods=["POST"])
def api_amigos_remover():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    outro = (dados.get("usuario") or "").strip()
    conexao = obter_bd()
    conexao.execute(
        "DELETE FROM amizades WHERE status = 'aceita' AND "
        "((solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE) "
        "OR (solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE))",
        (usuario, outro, outro, usuario),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/amigos/bloquear", methods=["POST"])
def api_amigos_bloquear():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    outro = (dados.get("usuario") or "").strip()
    if not buscar_usuario(outro):
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao = obter_bd()
    conexao.execute("INSERT OR IGNORE INTO bloqueios (usuario, bloqueado, criado_em) VALUES (?, ?, ?)", (usuario, outro, datetime.now().isoformat()))
    conexao.execute(
        "DELETE FROM amizades WHERE (solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE) "
        "OR (solicitante = ? COLLATE NOCASE AND destinatario = ? COLLATE NOCASE)",
        (usuario, outro, outro, usuario),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/amigos/desbloquear", methods=["POST"])
def api_amigos_desbloquear():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    outro = (dados.get("usuario") or "").strip()
    conexao = obter_bd()
    conexao.execute("DELETE FROM bloqueios WHERE usuario = ? COLLATE NOCASE AND bloqueado = ? COLLATE NOCASE", (usuario, outro))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: mensagens diretas (DM)
# =====================================================================

@app.route("/api/dm/<contato>/mensagens")
def api_dm_mensagens(contato):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    alvo = buscar_usuario(contato)
    if not alvo:
        return jsonify({"mensagens": []})
    conversa = id_conversa_dm(usuario, alvo["usuario"])
    conexao = obter_bd()
    linhas = conexao.execute("SELECT * FROM dm_mensagens WHERE conversa = ? ORDER BY id ASC LIMIT 300", (conversa,)).fetchall()
    conexao.close()
    ids = [l["id"] for l in linhas]
    reacoes_por_msg = montar_reacoes("dm", ids, usuario)
    mensagens = []
    for l in linhas:
        remetente_linha = buscar_usuario(l["remetente"])
        mensagens.append({
            "id": l["id"], "remetente": l["remetente"], "nome_exibicao": l["remetente"],
            "conteudo": l["conteudo"], "criado_em": l["criado_em"], "editado_em": l["editado_em"],
            "avatar": avatar_de(remetente_linha) if remetente_linha else AVATAR_PADRAO + l["remetente"],
            "minha": l["remetente"].lower() == usuario.lower(), "reacoes": reacoes_por_msg.get(l["id"], []),
            "fixada": False,
        })
    return jsonify({"mensagens": mensagens})


@app.route("/api/dm/<contato>/enviar", methods=["POST"])
def api_dm_enviar(contato):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    alvo = buscar_usuario(contato)
    if not alvo:
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    if usuarios_sao_bloqueados(usuario, alvo["usuario"]):
        return jsonify({"ok": False, "erro": "Voce nao pode enviar mensagens para este usuario."})
    dados = request.get_json() or {}
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False, "erro": "Mensagem vazia."})
    conversa = id_conversa_dm(usuario, alvo["usuario"])
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO dm_mensagens (conversa, remetente, destinatario, tipo, conteudo, criado_em) VALUES (?, ?, ?, 'texto', ?, ?)",
        (conversa, usuario, alvo["usuario"], texto, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/dm/mensagens/<int:mensagem_id>/editar", methods=["POST"])
def api_dm_editar(mensagem_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False})
    conexao = obter_bd()
    msg = conexao.execute("SELECT * FROM dm_mensagens WHERE id = ?", (mensagem_id,)).fetchone()
    if not msg or msg["remetente"].lower() != usuario.lower():
        conexao.close()
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    conexao.execute("UPDATE dm_mensagens SET conteudo = ?, editado_em = ? WHERE id = ?", (texto, datetime.now().isoformat(), mensagem_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/dm/mensagens/<int:mensagem_id>/excluir", methods=["POST"])
def api_dm_excluir(mensagem_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    msg = conexao.execute("SELECT * FROM dm_mensagens WHERE id = ?", (mensagem_id,)).fetchone()
    if not msg or msg["remetente"].lower() != usuario.lower():
        conexao.close()
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    conexao.execute("DELETE FROM dm_mensagens WHERE id = ?", (mensagem_id,))
    conexao.execute("DELETE FROM reacoes WHERE tipo = 'dm' AND mensagem_id = ?", (mensagem_id,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: servidores
# =====================================================================

@app.route("/api/servidores")
def api_servidores_listar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT s.* FROM servidores s JOIN servidor_membros m ON m.servidor_id = s.id "
        "WHERE m.usuario = ? COLLATE NOCASE ORDER BY m.entrou_em ASC",
        (usuario,),
    ).fetchall()
    conexao.close()
    return jsonify([{"id": l["id"], "nome": l["nome"], "icone": l["icone"], "impulsionado": bool(l["impulsionado"])} for l in linhas])


@app.route("/api/servidores/criar", methods=["POST"])
def api_servidores_criar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    nome = (request.form.get("nome") or "").strip()
    if not nome:
        return jsonify({"ok": False, "erro": "Digite um nome pro servidor."})
    icone = None
    arquivo = request.files.get("icone")
    if arquivo and arquivo.filename:
        icone = salvar_imagem_enviada(arquivo)
    codigo = gerar_codigo_convite()
    agora = datetime.now().isoformat()
    conexao = obter_bd()
    cursor = conexao.execute(
        "INSERT INTO servidores (nome, icone, dono, codigo_convite, criado_em) VALUES (?, ?, ?, ?, ?)",
        (nome, icone, usuario, codigo, agora),
    )
    servidor_id = cursor.lastrowid
    conexao.execute("INSERT INTO servidor_membros (servidor_id, usuario, entrou_em) VALUES (?, ?, ?)", (servidor_id, usuario, agora))
    conexao.execute("INSERT INTO canais (servidor_id, nome, tipo, ordem, criado_em) VALUES (?, 'geral', 'texto', 0, ?)", (servidor_id, agora))
    conexao.execute("INSERT INTO canais (servidor_id, nome, tipo, ordem, criado_em) VALUES (?, 'Geral', 'voz', 1, ?)", (servidor_id, agora))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "servidor_id": servidor_id})


@app.route("/api/servidores/entrar", methods=["POST"])
def api_servidores_entrar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    codigo = (dados.get("codigo") or "").strip()
    conexao = obter_bd()
    servidor = conexao.execute("SELECT * FROM servidores WHERE codigo_convite = ?", (codigo,)).fetchone()
    if not servidor:
        conexao.close()
        return jsonify({"ok": False, "erro": "Codigo de convite invalido."})
    banido = conexao.execute(
        "SELECT 1 FROM servidor_banidos WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor["id"], usuario)
    ).fetchone()
    if banido:
        conexao.close()
        return jsonify({"ok": False, "erro": "Voce foi banido deste servidor."})
    conexao.execute(
        "INSERT OR IGNORE INTO servidor_membros (servidor_id, usuario, entrou_em) VALUES (?, ?, ?)",
        (servidor["id"], usuario, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True, "servidor_id": servidor["id"]})


@app.route("/api/servidores/<int:servidor_id>")
def api_servidores_detalhe(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not eh_membro_do_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "Voce nao faz parte deste servidor."}), 403
    conexao = obter_bd()
    servidor = conexao.execute("SELECT * FROM servidores WHERE id = ?", (servidor_id,)).fetchone()
    if not servidor:
        conexao.close()
        return jsonify({"ok": False, "erro": "Servidor nao encontrado."}), 404
    categorias = conexao.execute(
        "SELECT * FROM categorias WHERE servidor_id = ? ORDER BY ordem ASC, id ASC", (servidor_id,)
    ).fetchall()
    canais = conexao.execute(
        "SELECT * FROM canais WHERE servidor_id = ? ORDER BY tipo ASC, ordem ASC, id ASC", (servidor_id,)
    ).fetchall()
    membros = conexao.execute(
        "SELECT usuario FROM servidor_membros WHERE servidor_id = ? ORDER BY usuario ASC", (servidor_id,)
    ).fetchall()
    apelidos = {
        l["usuario"].lower(): l["apelido"]
        for l in conexao.execute("SELECT usuario, apelido FROM apelidos_servidor WHERE servidor_id = ?", (servidor_id,)).fetchall()
    }
    cargos_por_membro = {}
    linhas_cargos = conexao.execute(
        "SELECT mc.usuario, c.id, c.nome, c.cor FROM membro_cargos mc JOIN cargos c ON c.id = mc.cargo_id "
        "WHERE mc.servidor_id = ? ORDER BY c.ordem ASC, c.id ASC",
        (servidor_id,),
    ).fetchall()
    for l in linhas_cargos:
        cargos_por_membro.setdefault(l["usuario"].lower(), []).append({"id": l["id"], "nome": l["nome"], "cor": l["cor"]})
    conexao.close()

    membros_info = []
    for m in membros:
        u = buscar_usuario(m["usuario"])
        membros_info.append({
            "usuario": m["usuario"], "apelido": apelidos.get(m["usuario"].lower()),
            "avatar": avatar_de(u) if u else AVATAR_PADRAO + m["usuario"],
            "online": esta_online(u["ultima_atividade"]) if u else False,
            "premium": bool(u and (u["premium"] or u["eh_admin"])),
            "cargos": cargos_por_membro.get(m["usuario"].lower(), []),
        })
    return jsonify({
        "ok": True, "nome": servidor["nome"], "icone": servidor["icone"], "descricao": servidor["descricao"],
        "codigo_convite": servidor["codigo_convite"], "sou_dono": servidor["dono"].lower() == usuario.lower(),
        "pode_gerenciar": pode_gerenciar_servidor(servidor_id, usuario),
        "verificado": bool(servidor["verificado"]), "impulsionado": bool(servidor["impulsionado"]),
        "publico": bool(servidor["publico"]),
        "categorias": [{"id": c["id"], "nome": c["nome"]} for c in categorias],
        "canais": [{"id": c["id"], "nome": c["nome"], "tipo": c["tipo"], "categoria_id": c["categoria_id"],
                    "topico": c["topico"], "slowmode": c["slowmode"] or 0} for c in canais],
        "membros": membros_info,
    })


@app.route("/api/servidores/<int:servidor_id>/editar", methods=["POST"])
def api_servidores_editar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode editar."}), 403
    nome = (request.form.get("nome") or "").strip()
    descricao = (request.form.get("descricao") or "").strip()
    publico = 1 if request.form.get("publico") == "1" else 0
    conexao = obter_bd()
    campos, valores = [], []
    if nome:
        campos.append("nome = ?")
        valores.append(nome)
    campos.append("descricao = ?")
    valores.append(descricao or None)
    campos.append("publico = ?")
    valores.append(publico)
    arquivo = request.files.get("icone")
    if arquivo and arquivo.filename:
        url = salvar_imagem_enviada(arquivo)
        if url:
            campos.append("icone = ?")
            valores.append(url)
    valores.append(servidor_id)
    conexao.execute(f"UPDATE servidores SET {', '.join(campos)} WHERE id = ?", valores)
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/sair", methods=["POST"])
def api_servidores_sair(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if eh_dono_do_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "O dono precisa excluir o servidor, nao pode simplesmente sair."})
    conexao = obter_bd()
    conexao.execute("DELETE FROM servidor_membros WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, usuario))
    conexao.execute("DELETE FROM membro_cargos WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, usuario))
    conexao.execute("DELETE FROM apelidos_servidor WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, usuario))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


def _excluir_servidor_completo(conexao, servidor_id):
    ids_canais = [r["id"] for r in conexao.execute("SELECT id FROM canais WHERE servidor_id = ?", (servidor_id,)).fetchall()]
    for canal_id in ids_canais:
        conexao.execute("DELETE FROM canal_mensagens WHERE canal_id = ?", (canal_id,))
        conexao.execute("DELETE FROM voz_presenca WHERE canal_id = ?", (canal_id,))
        conexao.execute("DELETE FROM voz_sinal WHERE canal_id = ?", (canal_id,))
    conexao.execute("DELETE FROM canais WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM categorias WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM servidor_membros WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM servidor_banidos WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM membro_cargos WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM cargos WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM apelidos_servidor WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM emojis_customizados WHERE servidor_id = ?", (servidor_id,))
    conexao.execute("DELETE FROM servidores WHERE id = ?", (servidor_id,))


@app.route("/api/servidores/<int:servidor_id>/excluir", methods=["POST"])
def api_servidores_excluir(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode exclui-lo."}), 403
    conexao = obter_bd()
    _excluir_servidor_completo(conexao, servidor_id)
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/membro/remover", methods=["POST"])
def api_servidores_remover_membro(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode remover membros."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("usuario") or "").strip()
    if alvo.lower() == usuario.lower():
        return jsonify({"ok": False, "erro": "Voce nao pode se remover assim."})
    conexao = obter_bd()
    conexao.execute("DELETE FROM servidor_membros WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, alvo))
    conexao.execute("DELETE FROM membro_cargos WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, alvo))
    conexao.execute("DELETE FROM apelidos_servidor WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/membro/banir", methods=["POST"])
def api_servidores_banir_membro(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode banir membros."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("usuario") or "").strip()
    if alvo.lower() == usuario.lower():
        return jsonify({"ok": False, "erro": "Voce nao pode se banir."})
    conexao = obter_bd()
    conexao.execute("DELETE FROM servidor_membros WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, alvo))
    conexao.execute("DELETE FROM membro_cargos WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, alvo))
    conexao.execute(
        "INSERT OR IGNORE INTO servidor_banidos (servidor_id, usuario, criado_em) VALUES (?, ?, ?)",
        (servidor_id, alvo, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/membro/desbanir", methods=["POST"])
def api_servidores_desbanir_membro(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode desbanir."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("usuario") or "").strip()
    conexao = obter_bd()
    conexao.execute("DELETE FROM servidor_banidos WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/banidos")
def api_servidores_banidos(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    if not pode_gerenciar_servidor(servidor_id, usuario_logado()):
        return jsonify([]), 403
    conexao = obter_bd()
    linhas = conexao.execute("SELECT usuario FROM servidor_banidos WHERE servidor_id = ?", (servidor_id,)).fetchall()
    conexao.close()
    return jsonify([{"usuario": l["usuario"], "avatar": avatar_de(l["usuario"])} for l in linhas])


@app.route("/api/servidores/<int:servidor_id>/apelido", methods=["POST"])
def api_servidores_apelido(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not eh_membro_do_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "Voce nao faz parte deste servidor."}), 403
    dados = request.get_json() or {}
    apelido = (dados.get("apelido") or "").strip()
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO apelidos_servidor (servidor_id, usuario, apelido) VALUES (?, ?, ?) "
        "ON CONFLICT(servidor_id, usuario) DO UPDATE SET apelido = excluded.apelido",
        (servidor_id, usuario, apelido or None),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: descoberta de servidores publicos
# =====================================================================

@app.route("/api/descobrir")
def api_descobrir():
    erro = exigir_login()
    if erro:
        return erro
    conexao = obter_bd()
    servidores = conexao.execute("SELECT * FROM servidores WHERE publico = 1 ORDER BY criado_em DESC LIMIT 100").fetchall()
    resultado = []
    for s in servidores:
        qtd = conexao.execute("SELECT COUNT(*) AS n FROM servidor_membros WHERE servidor_id = ?", (s["id"],)).fetchone()["n"]
        resultado.append({
            "id": s["id"], "nome": s["nome"], "icone": s["icone"], "descricao": s["descricao"],
            "membros": qtd, "verificado": bool(s["verificado"]),
        })
    conexao.close()
    return jsonify(resultado)


@app.route("/api/descobrir/entrar", methods=["POST"])
def api_descobrir_entrar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    try:
        servidor_id = int(dados.get("servidor_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    conexao = obter_bd()
    servidor = conexao.execute("SELECT * FROM servidores WHERE id = ? AND publico = 1", (servidor_id,)).fetchone()
    if not servidor:
        conexao.close()
        return jsonify({"ok": False, "erro": "Servidor nao encontrado ou nao e publico."})
    banido = conexao.execute(
        "SELECT 1 FROM servidor_banidos WHERE servidor_id = ? AND usuario = ? COLLATE NOCASE", (servidor_id, usuario)
    ).fetchone()
    if banido:
        conexao.close()
        return jsonify({"ok": False, "erro": "Voce foi banido deste servidor."})
    conexao.execute(
        "INSERT OR IGNORE INTO servidor_membros (servidor_id, usuario, entrou_em) VALUES (?, ?, ?)",
        (servidor_id, usuario, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: categorias
# =====================================================================

@app.route("/api/servidores/<int:servidor_id>/categorias/criar", methods=["POST"])
def api_categorias_criar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode criar categorias."}), 403
    dados = request.get_json() or {}
    nome = (dados.get("nome") or "").strip().upper()
    if not nome:
        return jsonify({"ok": False, "erro": "Digite um nome."})
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO categorias (servidor_id, nome, ordem, criado_em) VALUES (?, ?, 0, ?)",
        (servidor_id, nome, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: canais (texto e voz)
# =====================================================================

@app.route("/api/servidores/<int:servidor_id>/canais/criar", methods=["POST"])
def api_canais_criar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode criar canais."}), 403
    dados = request.get_json() or {}
    nome = (dados.get("nome") or "").strip()
    tipo = dados.get("tipo") if dados.get("tipo") in ("texto", "voz") else "texto"
    categoria_id = dados.get("categoria_id")
    try:
        categoria_id = int(categoria_id) if categoria_id else None
    except (TypeError, ValueError):
        categoria_id = None
    if not nome:
        return jsonify({"ok": False, "erro": "Digite um nome pro canal."})
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO canais (servidor_id, categoria_id, nome, tipo, ordem, criado_em) VALUES (?, ?, ?, ?, 0, ?)",
        (servidor_id, categoria_id, nome, tipo, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/<int:canal_id>/editar", methods=["POST"])
def api_canais_editar(canal_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    servidor_id = canal_pertence_a_membro(canal_id, usuario)
    if not servidor_id or not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode editar canais."}), 403
    dados = request.get_json() or {}
    nome = (dados.get("nome") or "").strip()
    topico = (dados.get("topico") or "").strip()
    try:
        slowmode = max(0, min(600, int(dados.get("slowmode") or 0)))
    except (TypeError, ValueError):
        slowmode = 0
    conexao = obter_bd()
    if nome:
        conexao.execute("UPDATE canais SET nome = ? WHERE id = ?", (nome, canal_id))
    if "categoria_id" in dados:
        categoria_id_bruta = dados.get("categoria_id")
        try:
            categoria_id = int(categoria_id_bruta) if categoria_id_bruta not in (None, "") else None
        except (TypeError, ValueError):
            categoria_id = None
        if categoria_id is not None:
            # a categoria precisa ser do mesmo servidor, senao ignora silenciosamente
            pertence = conexao.execute("SELECT 1 FROM categorias WHERE id = ? AND servidor_id = ?", (categoria_id, servidor_id)).fetchone()
            if not pertence:
                categoria_id = None
        conexao.execute("UPDATE canais SET categoria_id = ? WHERE id = ?", (categoria_id, canal_id))
    conexao.execute("UPDATE canais SET topico = ?, slowmode = ? WHERE id = ?", (topico or None, slowmode, canal_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/<int:canal_id>/excluir", methods=["POST"])
def api_canais_excluir(canal_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    servidor_id = canal_pertence_a_membro(canal_id, usuario)
    if not servidor_id or not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode excluir canais."}), 403
    conexao = obter_bd()
    conexao.execute("DELETE FROM canais WHERE id = ?", (canal_id,))
    conexao.execute("DELETE FROM canal_mensagens WHERE canal_id = ?", (canal_id,))
    conexao.execute("DELETE FROM voz_presenca WHERE canal_id = ?", (canal_id,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/categorias/<int:categoria_id>/editar", methods=["POST"])
def api_categorias_editar(categoria_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    categoria = conexao.execute("SELECT * FROM categorias WHERE id = ?", (categoria_id,)).fetchone()
    if not categoria or not pode_gerenciar_servidor(categoria["servidor_id"], usuario):
        conexao.close()
        return jsonify({"ok": False, "erro": "So o dono do servidor pode renomear categorias."}), 403
    nome = ((request.get_json() or {}).get("nome") or "").strip()
    if not nome:
        conexao.close()
        return jsonify({"ok": False, "erro": "Digite um nome."})
    conexao.execute("UPDATE categorias SET nome = ? WHERE id = ?", (nome, categoria_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/categorias/<int:categoria_id>/excluir", methods=["POST"])
def api_categorias_excluir(categoria_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    categoria = conexao.execute("SELECT * FROM categorias WHERE id = ?", (categoria_id,)).fetchone()
    if not categoria or not pode_gerenciar_servidor(categoria["servidor_id"], usuario):
        conexao.close()
        return jsonify({"ok": False, "erro": "So o dono do servidor pode excluir categorias."}), 403
    conexao.execute("UPDATE canais SET categoria_id = NULL WHERE categoria_id = ?", (categoria_id,))
    conexao.execute("DELETE FROM categorias WHERE id = ?", (categoria_id,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/<int:canal_id>/mensagens")
def api_canal_mensagens(canal_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    servidor_id = canal_pertence_a_membro(canal_id, usuario)
    if not servidor_id:
        return jsonify({"ok": False, "erro": "Sem acesso a este canal."}), 403
    conexao = obter_bd()
    linhas = conexao.execute("SELECT * FROM canal_mensagens WHERE canal_id = ? ORDER BY id ASC LIMIT 300", (canal_id,)).fetchall()
    emojis = conexao.execute("SELECT id, nome, url FROM emojis_customizados WHERE servidor_id = ?", (servidor_id,)).fetchall()
    conexao.close()
    ids = [l["id"] for l in linhas]
    reacoes_por_msg = montar_reacoes("canal", ids, usuario)
    pode_gerenciar = pode_gerenciar_servidor(servidor_id, usuario)
    mensagens = []
    for l in linhas:
        remetente_linha = buscar_usuario(l["remetente"])
        mensagens.append({
            "id": l["id"], "remetente": l["remetente"],
            "nome_exibicao": nome_exibicao_no_servidor(servidor_id, l["remetente"]),
            "conteudo": l["conteudo"], "criado_em": l["criado_em"], "editado_em": l["editado_em"],
            "fixada": bool(l["fixada"]),
            "avatar": avatar_de(remetente_linha) if remetente_linha else AVATAR_PADRAO + l["remetente"],
            "minha": l["remetente"].lower() == usuario.lower(), "pode_gerenciar": pode_gerenciar,
            "reacoes": reacoes_por_msg.get(l["id"], []),
        })
    return jsonify({
        "ok": True, "mensagens": mensagens,
        "emojis": [{"id": e["id"], "nome": e["nome"], "url": e["url"]} for e in emojis],
    })


def _pode_enviar_respeitando_slowmode(canal_id, usuario):
    conexao = obter_bd()
    canal = conexao.execute("SELECT slowmode FROM canais WHERE id = ?", (canal_id,)).fetchone()
    slowmode = canal["slowmode"] if canal else 0
    if not slowmode:
        conexao.close()
        return True, 0
    ultima = conexao.execute(
        "SELECT criado_em FROM canal_mensagens WHERE canal_id = ? AND remetente = ? COLLATE NOCASE ORDER BY id DESC LIMIT 1",
        (canal_id, usuario),
    ).fetchone()
    conexao.close()
    if not ultima:
        return True, 0
    passado = (datetime.now() - datetime.fromisoformat(ultima["criado_em"])).total_seconds()
    if passado >= slowmode:
        return True, 0
    return False, int(slowmode - passado)


@app.route("/api/canais/<int:canal_id>/enviar", methods=["POST"])
def api_canal_enviar(canal_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    servidor_id = canal_pertence_a_membro(canal_id, usuario)
    if not servidor_id:
        return jsonify({"ok": False, "erro": "Sem acesso a este canal."}), 403
    dados = request.get_json() or {}
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False})
    if not pode_gerenciar_servidor(servidor_id, usuario):
        pode, espera = _pode_enviar_respeitando_slowmode(canal_id, usuario)
        if not pode:
            return jsonify({"ok": False, "erro": f"Modo lento ativo: espere {espera}s para enviar de novo."})
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO canal_mensagens (canal_id, remetente, conteudo, criado_em) VALUES (?, ?, ?, ?)",
        (canal_id, usuario, texto, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/mensagens/<int:mensagem_id>/editar", methods=["POST"])
def api_canal_msg_editar(mensagem_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False})
    conexao = obter_bd()
    msg = conexao.execute("SELECT * FROM canal_mensagens WHERE id = ?", (mensagem_id,)).fetchone()
    if not msg or msg["remetente"].lower() != usuario.lower():
        conexao.close()
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    conexao.execute("UPDATE canal_mensagens SET conteudo = ?, editado_em = ? WHERE id = ?", (texto, datetime.now().isoformat(), mensagem_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/mensagens/<int:mensagem_id>/excluir", methods=["POST"])
def api_canal_msg_excluir(mensagem_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    msg = conexao.execute("SELECT * FROM canal_mensagens WHERE id = ?", (mensagem_id,)).fetchone()
    if not msg:
        conexao.close()
        return jsonify({"ok": False}), 404
    servidor_id = conexao.execute("SELECT servidor_id FROM canais WHERE id = ?", (msg["canal_id"],)).fetchone()["servidor_id"]
    conexao.close()
    if msg["remetente"].lower() != usuario.lower() and not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    conexao = obter_bd()
    conexao.execute("DELETE FROM canal_mensagens WHERE id = ?", (mensagem_id,))
    conexao.execute("DELETE FROM reacoes WHERE tipo = 'canal' AND mensagem_id = ?", (mensagem_id,))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/mensagens/<int:mensagem_id>/fixar", methods=["POST"])
def api_canal_msg_fixar(mensagem_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    msg = conexao.execute("SELECT * FROM canal_mensagens WHERE id = ?", (mensagem_id,)).fetchone()
    if not msg:
        conexao.close()
        return jsonify({"ok": False}), 404
    servidor_id = conexao.execute("SELECT servidor_id FROM canais WHERE id = ?", (msg["canal_id"],)).fetchone()["servidor_id"]
    conexao.close()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode fixar mensagens."}), 403
    dados = request.get_json() or {}
    fixar = 1 if dados.get("fixar") else 0
    conexao = obter_bd()
    conexao.execute("UPDATE canal_mensagens SET fixada = ? WHERE id = ?", (fixar, mensagem_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/canais/<int:canal_id>/fixadas")
def api_canal_fixadas(canal_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not canal_pertence_a_membro(canal_id, usuario):
        return jsonify({"mensagens": []}), 403
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT * FROM canal_mensagens WHERE canal_id = ? AND fixada = 1 ORDER BY id DESC", (canal_id,)
    ).fetchall()
    conexao.close()
    return jsonify({"mensagens": [{
        "id": l["id"], "remetente": l["remetente"], "conteudo": l["conteudo"],
        "avatar": avatar_de(l["remetente"]),
    } for l in linhas]})


# =====================================================================
# API: reacoes
# =====================================================================

@app.route("/api/mensagens/reagir", methods=["POST"])
def api_mensagens_reagir():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    tipo = dados.get("tipo")
    try:
        mensagem_id = int(dados.get("mensagem_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    emoji = (dados.get("emoji") or "").strip()
    if tipo not in ("canal", "dm") or not emoji or emoji not in EMOJIS_REACAO:
        return jsonify({"ok": False, "erro": "Reacao invalida."})
    conexao = obter_bd()
    ja_reagiu = conexao.execute(
        "SELECT 1 FROM reacoes WHERE tipo = ? AND mensagem_id = ? AND usuario = ? COLLATE NOCASE AND emoji = ?",
        (tipo, mensagem_id, usuario, emoji),
    ).fetchone()
    if ja_reagiu:
        conexao.execute(
            "DELETE FROM reacoes WHERE tipo = ? AND mensagem_id = ? AND usuario = ? COLLATE NOCASE AND emoji = ?",
            (tipo, mensagem_id, usuario, emoji),
        )
    else:
        conexao.execute(
            "INSERT OR IGNORE INTO reacoes (tipo, mensagem_id, usuario, emoji, criado_em) VALUES (?, ?, ?, ?, ?)",
            (tipo, mensagem_id, usuario, emoji, datetime.now().isoformat()),
        )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: cargos
# =====================================================================

@app.route("/api/servidores/<int:servidor_id>/cargos")
def api_cargos_listar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    if not eh_membro_do_servidor(servidor_id, usuario_logado()):
        return jsonify([]), 403
    conexao = obter_bd()
    cargos = conexao.execute("SELECT * FROM cargos WHERE servidor_id = ? ORDER BY ordem ASC, id ASC", (servidor_id,)).fetchall()
    conexao.close()
    return jsonify([{"id": c["id"], "nome": c["nome"], "cor": c["cor"]} for c in cargos])


@app.route("/api/servidores/<int:servidor_id>/cargos/criar", methods=["POST"])
def api_cargos_criar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode criar cargos."}), 403
    dados = request.get_json() or {}
    nome = (dados.get("nome") or "").strip()
    cor = (dados.get("cor") or "#99aab5").strip()
    if not nome:
        return jsonify({"ok": False, "erro": "Digite um nome pro cargo."})
    conexao = obter_bd()
    conexao.execute("INSERT INTO cargos (servidor_id, nome, cor, criado_em) VALUES (?, ?, ?, ?)", (servidor_id, nome, cor, datetime.now().isoformat()))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/cargos/<int:cargo_id>/excluir", methods=["POST"])
def api_cargos_excluir(servidor_id, cargo_id):
    erro = exigir_login()
    if erro:
        return erro
    if not pode_gerenciar_servidor(servidor_id, usuario_logado()):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    conexao = obter_bd()
    conexao.execute("DELETE FROM membro_cargos WHERE cargo_id = ? AND servidor_id = ?", (cargo_id, servidor_id))
    conexao.execute("DELETE FROM cargos WHERE id = ? AND servidor_id = ?", (cargo_id, servidor_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/cargos/atribuir", methods=["POST"])
def api_cargos_atribuir(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode atribuir cargos."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("usuario") or "").strip()
    try:
        cargo_id = int(dados.get("cargo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    if not eh_membro_do_servidor(servidor_id, alvo):
        return jsonify({"ok": False, "erro": "Essa pessoa nao faz parte do servidor."})
    conexao = obter_bd()
    conexao.execute("INSERT OR IGNORE INTO membro_cargos (servidor_id, usuario, cargo_id) VALUES (?, ?, ?)", (servidor_id, alvo, cargo_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: emojis customizados do servidor
# =====================================================================

@app.route("/api/servidores/<int:servidor_id>/emojis")
def api_emojis_listar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    if not eh_membro_do_servidor(servidor_id, usuario_logado()):
        return jsonify({"emojis": [], "limite": EMOJIS_SLOTS_NORMAL, "impulsionado": False}), 403
    conexao = obter_bd()
    emojis = conexao.execute("SELECT * FROM emojis_customizados WHERE servidor_id = ? ORDER BY id ASC", (servidor_id,)).fetchall()
    servidor = conexao.execute("SELECT impulsionado FROM servidores WHERE id = ?", (servidor_id,)).fetchone()
    conexao.close()
    impulsionado = bool(servidor and servidor["impulsionado"])
    limite = EMOJIS_SLOTS_IMPULSIONADO if impulsionado else EMOJIS_SLOTS_NORMAL
    return jsonify({
        "emojis": [{"id": e["id"], "nome": e["nome"], "url": e["url"]} for e in emojis],
        "limite": limite, "impulsionado": impulsionado,
    })


@app.route("/api/servidores/<int:servidor_id>/emojis/criar", methods=["POST"])
def api_emojis_criar(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    if not pode_gerenciar_servidor(servidor_id, usuario):
        return jsonify({"ok": False, "erro": "So o dono do servidor pode adicionar emojis."}), 403
    nome = re.sub(r"[^a-zA-Z0-9_]", "", (request.form.get("nome") or "").strip())
    arquivo = request.files.get("arquivo")
    if not nome or not arquivo:
        return jsonify({"ok": False, "erro": "Preencha o nome e escolha uma imagem."})
    conexao = obter_bd()
    servidor = conexao.execute("SELECT impulsionado FROM servidores WHERE id = ?", (servidor_id,)).fetchone()
    limite = EMOJIS_SLOTS_IMPULSIONADO if (servidor and servidor["impulsionado"]) else EMOJIS_SLOTS_NORMAL
    qtd_atual = conexao.execute("SELECT COUNT(*) AS n FROM emojis_customizados WHERE servidor_id = ?", (servidor_id,)).fetchone()["n"]
    if qtd_atual >= limite:
        conexao.close()
        return jsonify({"ok": False, "erro": f"Limite de {limite} emojis atingido. Peça a um administrador para impulsionar o servidor."})
    url = salvar_imagem_enviada(arquivo)
    if not url:
        conexao.close()
        return jsonify({"ok": False, "erro": "Nao foi possivel salvar a imagem."})
    conexao.execute(
        "INSERT INTO emojis_customizados (servidor_id, nome, url, criado_em) VALUES (?, ?, ?, ?)",
        (servidor_id, nome, url, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/servidores/<int:servidor_id>/emojis/<int:emoji_id>/excluir", methods=["POST"])
def api_emojis_excluir(servidor_id, emoji_id):
    erro = exigir_login()
    if erro:
        return erro
    if not pode_gerenciar_servidor(servidor_id, usuario_logado()):
        return jsonify({"ok": False, "erro": "Sem permissao."}), 403
    conexao = obter_bd()
    conexao.execute("DELETE FROM emojis_customizados WHERE id = ? AND servidor_id = ?", (emoji_id, servidor_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: digitando / nao lidos
# =====================================================================

@app.route("/api/digitando", methods=["POST"])
def api_digitando_avisar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    tipo = dados.get("tipo")
    alvo = str(dados.get("alvo") or "")
    if tipo not in ("canal", "dm") or not alvo:
        return jsonify({"ok": False}), 400
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO digitando (tipo, alvo, usuario, atualizado_em) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(tipo, alvo, usuario) DO UPDATE SET atualizado_em = excluded.atualizado_em",
        (tipo, alvo, usuario, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/digitando")
def api_digitando_listar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    tipo = request.args.get("tipo")
    alvo = request.args.get("alvo") or ""
    if tipo not in ("canal", "dm"):
        return jsonify([])
    limite = (datetime.now() - timedelta(seconds=SEGUNDOS_DIGITANDO_VALE)).isoformat()
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT usuario FROM digitando WHERE tipo = ? AND alvo = ? AND atualizado_em >= ? AND usuario != ? COLLATE NOCASE",
        (tipo, alvo, limite, usuario),
    ).fetchall()
    conexao.close()
    return jsonify([l["usuario"] for l in linhas])


@app.route("/api/leitura/marcar", methods=["POST"])
def api_leitura_marcar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    tipo = dados.get("tipo")
    alvo = str(dados.get("alvo") or "")
    try:
        ultimo_id = int(dados.get("ultimo_id") or 0)
    except (TypeError, ValueError):
        ultimo_id = 0
    if tipo not in ("canal", "dm") or not alvo or not ultimo_id:
        return jsonify({"ok": False}), 400
    marcar_lido(usuario, tipo, alvo, ultimo_id)
    return jsonify({"ok": True})


@app.route("/api/nao_lidos")
def api_nao_lidos():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    leituras = {
        (l["tipo"], l["alvo"]): l["ultima_msg_id"]
        for l in conexao.execute("SELECT tipo, alvo, ultima_msg_id FROM leituras WHERE usuario = ? COLLATE NOCASE", (usuario,)).fetchall()
    }

    dms_nao_lidos = []
    amigos = conexao.execute(
        "SELECT * FROM amizades WHERE status = 'aceita' AND (solicitante = ? COLLATE NOCASE OR destinatario = ? COLLATE NOCASE)",
        (usuario, usuario),
    ).fetchall()
    for a in amigos:
        outro = a["destinatario"] if a["solicitante"].lower() == usuario.lower() else a["solicitante"]
        conversa = id_conversa_dm(usuario, outro)
        ultima = conexao.execute(
            "SELECT MAX(id) AS m FROM dm_mensagens WHERE conversa = ? AND remetente != ? COLLATE NOCASE",
            (conversa, usuario),
        ).fetchone()["m"]
        if ultima and ultima > leituras.get(("dm", conversa), leituras.get(("dm", outro), 0)):
            # tambem verifica por conversa (a chave usada ao marcar e o nome do outro usuario, nao o id da conversa)
            if ultima > leituras.get(("dm", outro), 0):
                dms_nao_lidos.append(outro)

    servidores_com_nao_lido = []
    canais_nao_lidos = []
    meus_servidores = conexao.execute(
        "SELECT servidor_id FROM servidor_membros WHERE usuario = ? COLLATE NOCASE", (usuario,)
    ).fetchall()
    for sm in meus_servidores:
        canais = conexao.execute(
            "SELECT id FROM canais WHERE servidor_id = ? AND tipo = 'texto'", (sm["servidor_id"],)
        ).fetchall()
        algum_nao_lido = False
        for c in canais:
            ultima = conexao.execute(
                "SELECT MAX(id) AS m FROM canal_mensagens WHERE canal_id = ? AND remetente != ? COLLATE NOCASE",
                (c["id"], usuario),
            ).fetchone()["m"]
            if ultima and ultima > leituras.get(("canal", str(c["id"])), 0):
                canais_nao_lidos.append(c["id"])
                algum_nao_lido = True
        if algum_nao_lido:
            servidores_com_nao_lido.append(sm["servidor_id"])
    conexao.close()
    return jsonify({"dms": dms_nao_lidos, "servidores": servidores_com_nao_lido, "canais": canais_nao_lidos})


# =====================================================================
# API: canais de voz (mesh WebRTC via sinalizacao por polling)
# =====================================================================

@app.route("/api/voz/entrar", methods=["POST"])
def api_voz_entrar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    try:
        canal_id = int(dados.get("canal_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    if not canal_pertence_a_membro(canal_id, usuario):
        return jsonify({"ok": False, "erro": "Sem acesso a este canal."}), 403
    conexao = obter_bd()
    conexao.execute(
        "INSERT OR REPLACE INTO voz_presenca (canal_id, usuario, entrou_em) VALUES (?, ?, ?)",
        (canal_id, usuario, datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/voz/sair", methods=["POST"])
def api_voz_sair():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json(silent=True) or {}
    try:
        canal_id = int(dados.get("canal_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    conexao = obter_bd()
    conexao.execute("DELETE FROM voz_presenca WHERE canal_id = ? AND usuario = ? COLLATE NOCASE", (canal_id, usuario))
    conexao.execute(
        "DELETE FROM voz_sinal WHERE canal_id = ? AND (de_usuario = ? COLLATE NOCASE OR para_usuario = ? COLLATE NOCASE)",
        (canal_id, usuario, usuario),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/voz/participantes")
def api_voz_participantes():
    erro = exigir_login()
    if erro:
        return erro
    try:
        canal_id = int(request.args.get("canal_id"))
    except (TypeError, ValueError):
        return jsonify([])
    conexao = obter_bd()
    linhas = conexao.execute("SELECT usuario FROM voz_presenca WHERE canal_id = ? ORDER BY entrou_em ASC", (canal_id,)).fetchall()
    conexao.close()
    resultado = []
    for l in linhas:
        u = buscar_usuario(l["usuario"])
        resultado.append({"usuario": l["usuario"], "avatar": avatar_de(u) if u else AVATAR_PADRAO + l["usuario"]})
    return jsonify(resultado)


@app.route("/api/voz/sinal", methods=["POST"])
def api_voz_sinal():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    try:
        canal_id = int(dados.get("canal_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    para = (dados.get("para") or "").strip()
    tipo = dados.get("tipo")
    conteudo = dados.get("dados")
    if not para or tipo not in ("oferta", "resposta", "candidato") or conteudo is None:
        return jsonify({"ok": False}), 400
    conexao = obter_bd()
    conexao.execute(
        "INSERT INTO voz_sinal (canal_id, de_usuario, para_usuario, tipo, dados, criado_em, consumido) VALUES (?, ?, ?, ?, ?, ?, 0)",
        (canal_id, usuario, para, tipo, json.dumps(conteudo), datetime.now().isoformat()),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/voz/sinais")
def api_voz_sinais():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    try:
        canal_id = int(request.args.get("canal_id"))
    except (TypeError, ValueError):
        return jsonify([])
    conexao = obter_bd()
    linhas = conexao.execute(
        "SELECT * FROM voz_sinal WHERE canal_id = ? AND para_usuario = ? COLLATE NOCASE AND consumido = 0 ORDER BY id ASC",
        (canal_id, usuario),
    ).fetchall()
    ids = [l["id"] for l in linhas]
    if ids:
        conexao.execute(f"UPDATE voz_sinal SET consumido = 1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        conexao.commit()
    conexao.close()
    return jsonify([{"id": l["id"], "de": l["de_usuario"], "tipo": l["tipo"], "dados": json.loads(l["dados"])} for l in linhas])


# =====================================================================
# API: chamadas de voz/video em DM (1 para 1)
# =====================================================================

@app.route("/api/chamada/iniciar", methods=["POST"])
def api_chamada_iniciar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    contato = (dados.get("contato") or "").strip()
    oferta = dados.get("oferta")
    com_video = bool(dados.get("com_video"))
    alvo = buscar_usuario(contato)
    if not alvo or not oferta:
        return jsonify({"ok": False, "erro": "Dados invalidos."})
    if usuarios_sao_bloqueados(usuario, alvo["usuario"]):
        return jsonify({"ok": False, "erro": "Voce nao pode ligar para este usuario."})
    conexao = obter_bd()
    conexao.execute(
        "UPDATE chamadas_dm SET status = 'encerrada' WHERE status IN ('chamando','aceita') "
        "AND ((quem_liga = ? AND quem_recebe = ?) OR (quem_liga = ? AND quem_recebe = ?))",
        (usuario, alvo["usuario"], alvo["usuario"], usuario),
    )
    cursor = conexao.execute(
        "INSERT INTO chamadas_dm (quem_liga, quem_recebe, oferta, com_video, status, criado_em) VALUES (?, ?, ?, ?, 'chamando', ?)",
        (usuario, alvo["usuario"], json.dumps(oferta), 1 if com_video else 0, datetime.now().isoformat()),
    )
    conexao.commit()
    chamada_id = cursor.lastrowid
    conexao.close()
    return jsonify({"ok": True, "chamada_id": chamada_id})


@app.route("/api/chamada/pendente")
def api_chamada_pendente():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    conexao.execute(
        "UPDATE chamadas_dm SET status = 'encerrada' WHERE status = 'chamando' AND criado_em < ?",
        ((datetime.now() - timedelta(seconds=45)).isoformat(),),
    )
    conexao.commit()
    linha = conexao.execute(
        "SELECT * FROM chamadas_dm WHERE quem_recebe = ? COLLATE NOCASE AND status = 'chamando' ORDER BY id DESC LIMIT 1",
        (usuario,),
    ).fetchone()
    conexao.close()
    if not linha:
        return jsonify({"chamada": None})
    return jsonify({"chamada": {"id": linha["id"], "de": linha["quem_liga"], "oferta": json.loads(linha["oferta"]), "com_video": bool(linha["com_video"])}})


@app.route("/api/chamada/responder", methods=["POST"])
def api_chamada_responder():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    chamada_id = dados.get("chamada_id")
    resposta = dados.get("resposta")
    aceitar = dados.get("aceitar", True)
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM chamadas_dm WHERE id = ? AND quem_recebe = ? COLLATE NOCASE", (chamada_id, usuario)).fetchone()
    if not linha:
        conexao.close()
        return jsonify({"ok": False, "erro": "Chamada nao encontrada."})
    if not aceitar:
        conexao.execute("UPDATE chamadas_dm SET status = 'recusada' WHERE id = ?", (chamada_id,))
    else:
        conexao.execute("UPDATE chamadas_dm SET status = 'aceita', resposta = ? WHERE id = ?", (json.dumps(resposta), chamada_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/chamada/status/<int:chamada_id>")
def api_chamada_status(chamada_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM chamadas_dm WHERE id = ? AND quem_liga = ? COLLATE NOCASE", (chamada_id, usuario)).fetchone()
    conexao.close()
    if not linha:
        return jsonify({"status": "encerrada"})
    resultado = {"status": linha["status"]}
    if linha["resposta"]:
        resultado["resposta"] = json.loads(linha["resposta"])
    return jsonify(resultado)


@app.route("/api/chamada/candidato", methods=["POST"])
def api_chamada_candidato():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json() or {}
    chamada_id = dados.get("chamada_id")
    candidato = dados.get("candidato")
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM chamadas_dm WHERE id = ?", (chamada_id,)).fetchone()
    if not linha:
        conexao.close()
        return jsonify({"ok": False})
    if linha["quem_liga"].lower() == usuario.lower():
        lista = json.loads(linha["candidatos_liga"] or "[]")
        lista.append(candidato)
        conexao.execute("UPDATE chamadas_dm SET candidatos_liga = ? WHERE id = ?", (json.dumps(lista), chamada_id))
    elif linha["quem_recebe"].lower() == usuario.lower():
        lista = json.loads(linha["candidatos_recebe"] or "[]")
        lista.append(candidato)
        conexao.execute("UPDATE chamadas_dm SET candidatos_recebe = ? WHERE id = ?", (json.dumps(lista), chamada_id))
    else:
        conexao.close()
        return jsonify({"ok": False})
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/chamada/candidatos/<int:chamada_id>")
def api_chamada_candidatos(chamada_id):
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    desde = int(request.args.get("desde", 0))
    conexao = obter_bd()
    linha = conexao.execute("SELECT * FROM chamadas_dm WHERE id = ?", (chamada_id,)).fetchone()
    conexao.close()
    if not linha:
        return jsonify({"candidatos": [], "status": "encerrada"})
    if linha["quem_liga"].lower() == usuario.lower():
        lista = json.loads(linha["candidatos_recebe"] or "[]")
    else:
        lista = json.loads(linha["candidatos_liga"] or "[]")
    return jsonify({"candidatos": lista[desde:], "total": len(lista), "status": linha["status"]})


@app.route("/api/chamada/encerrar", methods=["POST"])
def api_chamada_encerrar():
    erro = exigir_login()
    if erro:
        return erro
    usuario = usuario_logado()
    dados = request.get_json(silent=True) or {}
    chamada_id = dados.get("chamada_id")
    conexao = obter_bd()
    conexao.execute(
        "UPDATE chamadas_dm SET status = 'encerrada' WHERE id = ? AND (quem_liga = ? COLLATE NOCASE OR quem_recebe = ? COLLATE NOCASE)",
        (chamada_id, usuario, usuario),
    )
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# API: painel do administrador (nada pago - liberacao manual)
# =====================================================================

@app.route("/api/admin/usuarios")
def api_admin_usuarios():
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify([]), 403
    conexao = obter_bd()
    linhas = conexao.execute("SELECT * FROM usuarios ORDER BY criado_em ASC").fetchall()
    conexao.close()
    return jsonify([{
        "usuario": l["usuario"], "id_publico": l["id_publico"], "avatar": avatar_de(l),
        "premium": bool(l["premium"]), "eh_admin": bool(l["eh_admin"]), "banido": bool(l["banido"]),
        "online": esta_online(l["ultima_atividade"]),
    } for l in linhas])


@app.route("/api/admin/usuarios/premium", methods=["POST"])
def api_admin_usuarios_premium():
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify({"ok": False, "erro": "So administradores podem fazer isso."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("alvo") or "").strip()
    conceder = bool(dados.get("conceder"))
    if not buscar_usuario(alvo):
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    conexao = obter_bd()
    conexao.execute("UPDATE usuarios SET premium = ? WHERE usuario = ? COLLATE NOCASE", (1 if conceder else 0, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/admin/usuarios/banir", methods=["POST"])
def api_admin_usuarios_banir():
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify({"ok": False, "erro": "So administradores podem fazer isso."}), 403
    dados = request.get_json() or {}
    alvo = (dados.get("alvo") or "").strip()
    banir = bool(dados.get("banir"))
    linha = buscar_usuario(alvo)
    if not linha:
        return jsonify({"ok": False, "erro": "Usuario nao encontrado."})
    if linha["eh_admin"] and banir:
        return jsonify({"ok": False, "erro": "Nao e possivel banir um administrador."})
    conexao = obter_bd()
    conexao.execute("UPDATE usuarios SET banido = ? WHERE usuario = ? COLLATE NOCASE", (1 if banir else 0, alvo))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/admin/servidores")
def api_admin_servidores():
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify([]), 403
    conexao = obter_bd()
    linhas = conexao.execute("SELECT * FROM servidores ORDER BY criado_em ASC").fetchall()
    resultado = []
    for l in linhas:
        qtd = conexao.execute("SELECT COUNT(*) AS n FROM servidor_membros WHERE servidor_id = ?", (l["id"],)).fetchone()["n"]
        resultado.append({
            "id": l["id"], "nome": l["nome"], "icone": l["icone"], "dono": l["dono"], "membros": qtd,
            "verificado": bool(l["verificado"]), "impulsionado": bool(l["impulsionado"]), "publico": bool(l["publico"]),
        })
    conexao.close()
    return jsonify(resultado)


@app.route("/api/admin/servidores/verificar", methods=["POST"])
def api_admin_servidores_verificar():
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify({"ok": False, "erro": "So administradores podem fazer isso."}), 403
    dados = request.get_json() or {}
    try:
        servidor_id = int(dados.get("servidor_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    verificar = bool(dados.get("verificar"))
    conexao = obter_bd()
    conexao.execute("UPDATE servidores SET verificado = ? WHERE id = ?", (1 if verificar else 0, servidor_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/admin/servidores/impulsionar", methods=["POST"])
def api_admin_servidores_impulsionar():
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify({"ok": False, "erro": "So administradores podem fazer isso."}), 403
    dados = request.get_json() or {}
    try:
        servidor_id = int(dados.get("servidor_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    impulsionar = bool(dados.get("impulsionar"))
    conexao = obter_bd()
    conexao.execute("UPDATE servidores SET impulsionado = ? WHERE id = ?", (1 if impulsionar else 0, servidor_id))
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


@app.route("/api/admin/servidores/<int:servidor_id>/excluir", methods=["POST"])
def api_admin_servidores_excluir(servidor_id):
    erro = exigir_login()
    if erro:
        return erro
    if not eh_admin(usuario_logado()):
        return jsonify({"ok": False, "erro": "So administradores podem fazer isso."}), 403
    conexao = obter_bd()
    _excluir_servidor_completo(conexao, servidor_id)
    conexao.commit()
    conexao.close()
    return jsonify({"ok": True})


# =====================================================================
# Ponto de entrada
# =====================================================================

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
