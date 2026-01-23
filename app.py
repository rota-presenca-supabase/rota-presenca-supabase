# SCRIPT COMPLETO – PODE EXECUTAR
# App Streamlit (idêntico ao do Sheets, porém usando Supabase/Postgres)

import streamlit as st
import pandas as pd
from datetime import datetime, time, timedelta, timezone
import pytz
from fpdf import FPDF
import urllib.parse
import time as time_module
import random
import re

from supabase import create_client, Client
from postgrest.exceptions import APIError as PostgrestAPIError
import smtplib
from email.message import EmailMessage

# ==========================================================
# CONFIGURAÇÃO
# ==========================================================
FUSO_BR = pytz.timezone("America/Sao_Paulo")

# Secrets esperados no Streamlit Cloud:
# SUPABASE_URL = "https://xxxx.supabase.co"
# SUPABASE_SERVICE_ROLE_KEY = "sb_secret_...."   (pode usar service_role, como você pediu)

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", st.secrets.get("SUPABASE_KEY", ""))  # fallback

# Tabelas no Supabase:
TB_USUARIOS = "usuarios"
TB_PRESENCA = "presencas"
TB_CONFIG = "config"

# ==========================================================
# GIF NO FINAL DA PÁGINA
# ==========================================================
GIF_URL = "https://www.imagensanimadas.com/data/media/425/onibus-imagem-animada-0024.gif"

# ==========================================================
# LISTAS FIXAS (como você pediu)
# ==========================================================
LISTA_GRAD = ["TCEL", "MAJ", "CAP", "1º TEN", "2º TEN", "SUBTEN", "1º SGT", "2º SGT", "3º SGT", "CB", "SD", "FC COM", "FC TER"]
LISTA_ORIGEM = ["QG", "RMCF", "OUTROS"]

# ==========================================================
# TELEFONE
# ==========================================================
def tel_only_digits(s: str) -> str:
    return re.sub(r"\D+", "", str(s or ""))

def tel_format_br(digits: str) -> str:
    d = tel_only_digits(digits)
    if len(d) >= 2:
        ddd = d[:2]
        rest = d[2:]
    else:
        return d

    if len(rest) >= 9:
        p1 = rest[:5]
        p2 = rest[5:9]
        return f"({ddd}) {p1}.{p2}"
    elif len(rest) > 5:
        p1 = rest[:5]
        p2 = rest[5:]
        return f"({ddd}) {p1}.{p2}"
    else:
        return f"({ddd}) {rest}"

def tel_is_valid_11(s: str) -> bool:
    return len(tel_only_digits(s)) == 11

# ==========================================================
# RETRY / BACKOFF (Supabase / PostgREST)
# ==========================================================
def sb_call(fn, *args, **kwargs):
    max_tries = 6
    base = 0.6
    last_err = None

    for attempt in range(max_tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            msg = str(e)
            is_rate = ("429" in msg) or ("Too Many" in msg) or ("rate" in msg.lower())
            is_5xx = any(code in msg for code in ["500", "502", "503", "504"])
            if is_rate or is_5xx:
                sleep_s = (base * (2 ** attempt)) + random.uniform(0.0, 0.35)
                time_module.sleep(min(sleep_s, 6.0))
                continue
            raise
    raise last_err

# ==========================================================
# SUPABASE CLIENT (cache)
# ==========================================================
@st.cache_resource
def sb() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Secrets do Supabase não encontrados. Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no Streamlit Secrets.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ==========================================================
# SENHA TEMPORÁRIA (1 acesso)
# ==========================================================
def _br_now():
    return datetime.now(FUSO_BR)

def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M:%S")

def _parse_dt(s: str):
    """Converte strings para datetime timezone-aware (BR_TZ). Aceita 'DD/MM/YYYY HH:MM:SS' e ISO."""
    if s is None:
        return None
    ss = str(s).strip()
    if not ss:
        return None
    # Formato legado do Sheets
    try:
        return FUSO_BR.localize(datetime.strptime(ss, "%d/%m/%Y %H:%M:%S"))
    except Exception:
        pass
    # ISO / Postgres
    try:
        if ss.endswith("Z"):
            ss = ss[:-1] + "+00:00"
        dt2 = datetime.fromisoformat(ss.replace("T", " "))
        if dt2.tzinfo is None:
            dt2 = BR_TZ.localize(dt2)
        return dt2
    except Exception:
        return None

def gerar_senha_temp(tam: int = 10) -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alfabeto) for _ in range(tam))

# ==========================================================
# EMAIL HELPERS (SMTP)
# - Configure no Streamlit Secrets (TOML):
#   EMAIL_HOST = "smtp.gmail.com"
#   EMAIL_PORT = 587
#   EMAIL_USER = "seu_email@gmail.com"
#   EMAIL_PASSWORD = "senha_de_app"
#   EMAIL_FROM = "seu_email@gmail.com"   # opcional
#   EMAIL_TLS = true                    # opcional (padrão true)
# ==========================================================
def _email_cfg():
    # st.secrets funciona no Streamlit Cloud
    s = getattr(st, "secrets", {})
    host = s.get("EMAIL_HOST")
    port = s.get("EMAIL_PORT")
    user = s.get("EMAIL_USER")
    pwd  = s.get("EMAIL_PASSWORD")
    if not host or not port or not user or not pwd:
        raise ValueError("Faltou configurar EMAIL_HOST/EMAIL_PORT/EMAIL_USER/EMAIL_PASSWORD no Secrets.")
    email_from = s.get("EMAIL_FROM") or user
    tls = s.get("EMAIL_TLS", True)
    try:
        port = int(port)
    except Exception:
        raise ValueError("EMAIL_PORT precisa ser número (ex: 587).")
    return {"host": host, "port": port, "user": user, "pwd": pwd, "from": email_from, "tls": bool(tls)}

def enviar_email(destinatario: str, assunto: str, corpo: str):
    cfg = _email_cfg()
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.set_content(corpo)

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
        if cfg["tls"]:
            server.starttls()
        server.login(cfg["user"], cfg["pwd"])
        server.send_message(msg)

def enviar_dados_cadastrais_para_email(u_any: dict):
    """Envia os dados cadastrais para o e-mail do próprio usuário.

    Aceita tanto o "row" vindo do banco (chaves minúsculas) quanto o dict de UI (ex: 'Email').
    """
    # pega e-mail independente de como veio o dict
    email = (
        (u_any or {}).get("email")
        or (u_any or {}).get("Email")
        or (u_any or {}).get("E-mail")
        or (u_any or {}).get("e-mail")
    )
    if not email:
        raise ValueError("Usuário sem email cadastrado.")

    nome = (
        (u_any or {}).get("nome")
        or (u_any or {}).get("Nome")
        or (u_any or {}).get("Nome de Escala")
        or "(sem nome)"
    )

    # Monta corpo de forma robusta (tanto UI quanto DB)
    def pick(*ks):
        for k in ks:
            if (u_any or {}).get(k) not in (None, ""):
                return (u_any or {}).get(k)
        return ""

    corpo = [
        f"Olá, {nome}.",
        "",
        "Segue abaixo o seu cadastro completo:",
        "",
        f"Nome de Escala: {pick('nome','Nome','Nome de Escala')}",
        f"E-mail: {email}",
        f"Telefone: {pick('telefone','Telefone','TELEFONE')}",
        f"Graduação: {pick('graduacao','Graduação')}",
        f"Lotação: {pick('lotacao','Lotação')}",
        f"Origem: {pick('origem','Origem','QG_RMCF_OUTROS')}",
        f"Senha: {pick('senha','Senha','SENHA')}",
        "",
        "(Este e-mail foi enviado automaticamente pelo sistema.)",
    ]
    assunto = "Seus dados cadastrais - Rota Nova Iguaçu"
    enviar_email(email, assunto, "\n".join(corpo))
def usuarios_select(where=None, columns="*"):
    q = sb().table(TB_USUARIOS).select(columns)
    if where:
        for k, v in where.items():
            q = q.eq(k, v)
    res = sb_call(q.execute)
    return res.data or []



def _parse_ts(ts_value):
    """Parse Supabase timestamptz (ISO string) into aware datetime."""
    if not ts_value:
        return None
    try:
        if isinstance(ts_value, datetime):
            dt = ts_value
        else:
            s = str(ts_value).strip()
            # Supabase can return 'Z'
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        # Ensure tz-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def pode_recuperar_dados_hoje(user_raw: dict):
    """Permite recuperar/enviar dados no máximo 1 vez por dia (fuso BR)."""
    last = user_raw.get("last_recuperacao_dados_at") or user_raw.get("ultima_recuperacao_dados_at")
    dt_last = _parse_ts(last)
    if not dt_last:
        return True, None

    now_br = datetime.now(FUSO_BR)
    last_br = dt_last.astimezone(FUSO_BR)
    if last_br.date() == now_br.date():
        # Próxima janela: 00:00 do dia seguinte (fuso BR)
        next_allowed = (now_br + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return False, next_allowed
    return True, None


def marcar_recuperacao_dados(user_id: str):
    """Marca o envio como realizado hoje."""
    try:
        supabase_admin.table("usuarios").update(
            {"last_recuperacao_dados_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user_id).execute()
    except Exception:
        # Se falhar, não bloqueia o usuário (apenas não registra o limite)
        pass

def try_acquire_recuperacao_token(user_id: str):
    """Garante no máximo 1 envio de dados por dia (fuso America/Sao_Paulo).
    Retorna (ok, prev_value, new_value, next_allowed_dt).
    """
    tz = pytz.timezone("America/Sao_Paulo")
    now = datetime.now(tz)
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    now_utc = now.astimezone(pytz.utc)
    start_day_utc = start_day.astimezone(pytz.utc)
    next_allowed = start_day + timedelta(days=1)

    # Busca valor atual (para rollback caso o e-mail falhe)
    prev = None
    try:
        r = sb().table("usuarios").select("last_recuperacao_dados_at").eq("id", user_id).limit(1).execute()
        if r.data and isinstance(r.data, list) and len(r.data) > 0:
            prev = r.data[0].get("last_recuperacao_dados_at")
    except Exception:
        prev = None

    # UPDATE condicional (só atualiza se ainda não recuperou hoje)
    cond = f"last_recuperacao_dados_at.is.null,last_recuperacao_dados_at.lt.{start_day_utc.isoformat()}"
    try:
        upd = (
            sb()
            .table("usuarios")
            .update({"last_recuperacao_dados_at": now_utc.isoformat()})
            .eq("id", user_id)
            .or_(cond)
            .execute()
        )
        ok = bool(upd.data) and isinstance(upd.data, list) and len(upd.data) > 0
        return ok, prev, now_utc.isoformat(), next_allowed
    except Exception:
        return False, prev, now_utc.isoformat(), next_allowed


def rollback_recuperacao_token(user_id: str, prev_value):
    """Se o envio do e-mail falhar, tenta restaurar o timestamp anterior."""
    try:
        sb().table("usuarios").update({"last_recuperacao_dados_at": prev_value}).eq("id", user_id).execute()
    except Exception:
        pass

def usuarios_insert(row: dict):
    res = sb_call(sb().table(TB_USUARIOS).insert, row).execute()
    return res.data

def usuarios_update(where: dict, patch: dict):
    q = sb().table(TB_USUARIOS).update(patch)
    for k, v in where.items():
        q = q.eq(k, v)
    res = sb_call(q.execute)
    return res.data

def usuarios_delete(where: dict):
    q = sb().table(TB_USUARIOS).delete()
    for k, v in where.items():
        q = q.eq(k, v)
    res = sb_call(q.execute)
    return res.data

def presenca_select(columns="*"):
    res = sb_call(sb().table(TB_PRESENCA).select(columns).order("data_hora", desc=False).execute)
    return res.data or []

def presenca_insert(row: dict):
    res = sb_call(sb().table(TB_PRESENCA).insert(row).execute)
    return res.data

def presenca_delete(where: dict = None):
    q = sb().table(TB_PRESENCA).delete()
    if where:
        for k, v in where.items():
            q = q.eq(k, v)
    res = sb_call(q.execute)
    return res.data

def presenca_exists(usuario_id: str, ciclo_data: str, ciclo_hora: str) -> bool:
    try:
        r = sb().table(TB_PRESENCA).select("id").eq("usuario_id", usuario_id).eq("ciclo_data", ciclo_data).eq("ciclo_hora", ciclo_hora).limit(1).execute()
        data = getattr(r, "data", None) or []
        return len(data) > 0
    except Exception:
        return False


def limpar_presencas_de_outros_ciclos(ciclo_data: str, ciclo_hora: str) -> None:
    """Mantém a tabela presencas 'limpa' para o ciclo atual.
    Remove registros de ciclos anteriores (ciclo_data/ciclo_hora diferentes do atual).
    """
    try:
        # PostgREST OR: campo.op.valor
        filtro = f"ciclo_data.neq.{ciclo_data},ciclo_hora.neq.{ciclo_hora}"
        sb().table(TB_PRESENCA).delete().or_(filtro).execute()
    except Exception:
        # Se a API não aceitar OR, não quebra o app (apenas não limpa)
        pass


def presenca_delete_usuario_ciclo(usuario_id: str, ciclo_data: str, ciclo_hora: str) -> None:
    sb().table(TB_PRESENCA).delete().eq("usuario_id", usuario_id).eq("ciclo_data", ciclo_data).eq("ciclo_hora", ciclo_hora).execute()

def config_get_int(key: str, default: int = 100) -> int:
    try:
        res = sb_call(sb().table(TB_CONFIG).select("value").eq("key", key).limit(1).execute)
        data = res.data or []
        if not data:
            # cria
            sb_call(sb().table(TB_CONFIG).insert({"key": key, "value": str(default)}).execute)
            return default
        return int(str(data[0].get("value", default)))
    except Exception:
        return default

def config_set_int(key: str, value: int):
    # upsert
    try:
        sb_call(sb().table(TB_CONFIG).upsert({"key": key, "value": str(int(value))}).execute)
    except Exception:
        # fallback update then insert
        try:
            usuarios_update({"key": key}, {"value": str(int(value))})
        except Exception:
            sb_call(sb().table(TB_CONFIG).insert({"key": key, "value": str(int(value))}).execute)

# ==========================================================


def buscar_user_by_email_senha(email: str, senha: str):
    """Busca usuário por Email + Senha REAL (não temporária)."""
    email = str(email or "").strip().lower()
    senha = str(senha or "").strip()
    if not email or not senha:
        return None, None
    try:
        rows = usuarios_select({"email": email, "senha": senha})
        if rows:
            u = rows[0]
            return u.get("id"), u
    except Exception:
        pass
    return None, None
# LEITURAS (cache_data)
# ==========================================================
@st.cache_data(ttl=30)
def buscar_usuarios_cadastrados():
    try:
        rows = usuarios_select()
        return [map_user_row(r) for r in rows]
    except Exception:
        return []

@st.cache_data(ttl=3)
def buscar_usuarios_admin():
    try:
        rows = usuarios_select()
        return [map_user_row(r) for r in rows]
    except Exception:
        return []

@st.cache_data(ttl=120)
def buscar_limite_dinamico():
    return config_get_int("limite_usuarios", 100)

@st.cache_data(ttl=6)
def buscar_presenca_atualizada():
    try:
        rows = presenca_select()
        return [map_presenca_row(r) for r in (rows or [])]
    except Exception:
        return []

def verificar_status_e_limpar_db(presencas_rows):
    """Retorna (is_aberto, janela_conferencia).

    Também faz a 'limpeza por marco' para não carregar presenças antigas quando muda o ciclo.
    - Marco 06:50 e 18:50 (horário de Brasília).
    - Se existir presença mais recente anterior ao marco atual, zera a tabela.
    """
    agora = _br_now()
    hora_atual = agora.time()

    # Define o marco (início do ciclo "atual") em horário BR
    if hora_atual >= time(18, 50):
        marco = agora.replace(hour=18, minute=50, second=0, microsecond=0)
    elif hora_atual >= time(6, 50):
        marco = agora.replace(hour=6, minute=50, second=0, microsecond=0)
    else:
        # antes de 06:50 -> ainda é o ciclo iniciado ontem 18:50
        marco = (agora - timedelta(days=1)).replace(hour=18, minute=50, second=0, microsecond=0)

    # 1) Limpeza automática (se houver presenças mais antigas que o marco atual)
    if presencas_rows:
        try:
            from dateutil import parser as _dtparser
            ultima_dt = None
            for r in presencas_rows or []:
                dh = r.get("data_hora")
                if not dh:
                    continue
                try:
                    dt_obj = _dtparser.isoparse(dh) if isinstance(dh, str) else dh
                except Exception:
                    continue
                if dt_obj is None:
                    continue
                if dt_obj.tzinfo is None:
                    dt_obj = dt_obj.replace(tzinfo=pytz.utc)
                if (ultima_dt is None) or (dt_obj > ultima_dt):
                    ultima_dt = dt_obj

            if ultima_dt is not None:
                ultima_dt_br = ultima_dt.astimezone(_BR_TZ)
                # se a última presença é anterior ao marco do ciclo atual, zera
                if ultima_dt_br < marco:
                    try:
                        supabase.table("presencas").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
                    except Exception:
                        # não derruba o app por falha de limpeza
                        pass
        except Exception:
            pass

    # 2) Status de janela aberta/fechada e janela de conferência
    # Regra: abre 06:30-07:00 (embarque manhã) e 18:30-19:00 (embarque tarde)
    is_aberto = False
    janela_conferencia = False

    if time(6, 30) <= hora_atual <= time(7, 0):
        is_aberto = True
    elif time(18, 30) <= hora_atual <= time(19, 0):
        is_aberto = True
    else:
        is_aberto = False

    # Janela de conferência: 07:00-07:10 e 19:00-19:10
    if time(7, 0) <= hora_atual <= time(7, 10):
        janela_conferencia = True
    elif time(19, 0) <= hora_atual <= time(19, 10):
        janela_conferencia = True

    return is_aberto, janela_conferencia

# ==========================================================
# CICLO (texto abaixo do título)
# ==========================================================
def obter_ciclo_atual():
    agora = _br_now()
    t = agora.time()
    wd = agora.weekday()

    em_fechamento_fds = (wd == 4 and t >= time(17, 0)) or (wd == 5) or (wd == 6 and t < time(19, 0))
    if em_fechamento_fds:
        dias_para_seg = (7 - wd) % 7
        alvo_dt = (agora + timedelta(days=dias_para_seg)).date()
        alvo_h = "06:30"
    else:
        if t >= time(19, 0):
            alvo_dt = (agora + timedelta(days=1)).date()
            alvo_h = "06:30"
        elif t < time(7, 0):
            alvo_dt = agora.date()
            alvo_h = "06:30"
        else:
            alvo_dt = agora.date()
            alvo_h = "18:30"

    return alvo_h, alvo_dt.strftime("%d/%m/%Y")

# ==========================================================
# ORDENAÇÃO (igual ao Sheets)
# ==========================================================
def aplicar_ordenacao(df):
    if "EMAIL" not in df.columns:
        df["EMAIL"] = "N/A"

    if "QG_RMCF_OUTROS" not in df.columns and "ORIGEM" in df.columns:
        df["QG_RMCF_OUTROS"] = df["ORIGEM"]
    if "QG_RMCF_OUTROS" not in df.columns:
        df["QG_RMCF_OUTROS"] = ""

    p_orig = {"QG": 1, "RMCF": 2, "OUTROS": 3}
    p_grad_normal = {
        "TCEL": 1, "MAJ": 2, "CAP": 3, "1º TEN": 4, "2º TEN": 5, "SUBTEN": 6,
        "1º SGT": 7, "2º SGT": 8, "3º SGT": 9, "CB": 10, "SD": 11
    }

    def grupo_fc(grad):
        g = str(grad or "").strip().upper()
        if g == "FC COM":
            return 1
        if g == "FC TER":
            return 2
        return 0

    df["grupo_fc"] = df["GRADUAÇÃO"].apply(grupo_fc)
    df["p_o"] = df["QG_RMCF_OUTROS"].map(p_orig).fillna(99)

    def p_grad(row):
        if int(row.get("grupo_fc", 0)) == 0:
            return p_grad_normal.get(str(row.get("GRADUAÇÃO", "")).strip().upper(), 999)
        return 0

    df["p_g"] = df.apply(p_grad, axis=1)
    df["dt"] = pd.to_datetime(df["DATA_HORA"], dayfirst=True, errors="coerce")

    df = df.sort_values(by=["grupo_fc", "p_o", "p_g", "dt"]).reset_index(drop=True)
    df.insert(0, "Nº", [str(i + 1) if i < 38 else f"Exc-{i - 37:02d}" for i in range(len(df))])

    df_v = df.copy()
    for i, r in df_v.iterrows():
        if "Exc-" in str(r["Nº"]):
            for c in df_v.columns:
                df_v.at[i, c] = f"<span style='color:#d32f2f; font-weight:bold;'>{r[c]}</span>"

    return df.drop(columns=["grupo_fc", "p_o", "p_g", "dt"]), df_v.drop(columns=["grupo_fc", "p_o", "p_g", "dt"])

# ==========================================================
# PDF
# ==========================================================
class PDFRelatorio(FPDF):
    def __init__(self, titulo="LISTA DE PRESENÇA", sub=None):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.titulo = titulo
        self.sub = sub or ""
        self.set_auto_page_break(auto=True, margin=12)
        self.alias_nb_pages()

    def header(self):
        self.set_font("Arial", "B", 14)
        self.cell(0, 8, self.titulo, ln=True, align="C")

        self.set_font("Arial", "", 9)
        if self.sub:
            self.cell(0, 5, self.sub, ln=True, align="C")
        self.ln(2)

        self.set_draw_color(180, 180, 180)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Arial", "", 8)
        self.set_text_color(90, 90, 90)
        self.cell(0, 6, f"Página {self.page_no()}/{{nb}} - Rota Nova Iguaçu", align="C")

def gerar_pdf_apresentado(df_o: pd.DataFrame, resumo: dict) -> bytes:
    agora = _br_now().strftime("%d/%m/%Y %H:%M:%S")
    sub = f"Emitido em: {agora}"

    pdf = PDFRelatorio(titulo="ROTA NOVA IGUAÇU - LISTA DE PRESENÇA", sub=sub)
    pdf.add_page()

    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, "RESUMO", ln=True, fill=True)

    pdf.set_font("Arial", "", 9)
    insc = resumo.get("inscritos", 0)
    vagas = resumo.get("vagas", 38)
    exc = max(0, insc - vagas)
    sobra = max(0, vagas - insc)

    pdf.cell(0, 6, f"Inscritos: {insc} | Vagas: {vagas} | Sobra: {sobra} | Excedentes: {exc}", ln=True)
    pdf.ln(2)

    headers = ["Nº", "GRADUAÇÃO", "NOME", "LOTAÇÃO", "ORIGEM"]
    col_w = [12, 26, 78, 55, 19]

    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(30, 30, 30)
    pdf.set_text_color(255, 255, 255)

    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=0, align="C", fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "", 8)

    for idx, (_, r) in enumerate(df_o.iterrows()):
        is_exc = "Exc-" in str(r.get("Nº", ""))
        if is_exc:
            pdf.set_fill_color(255, 235, 238)
        else:
            pdf.set_fill_color(245, 245, 245 if idx % 2 == 0 else 255)

        origem = str(r.get("QG_RMCF_OUTROS", "") or r.get("ORIGEM", "") or "").strip()

        pdf.cell(col_w[0], 6, str(r.get("Nº", "")), border=0, fill=True)
        pdf.cell(col_w[1], 6, str(r.get("GRADUAÇÃO", "")), border=0, fill=True)
        pdf.cell(col_w[2], 6, str(r.get("NOME", ""))[:42], border=0, fill=True)
        pdf.cell(col_w[3], 6, str(r.get("LOTAÇÃO", ""))[:34], border=0, fill=True)
        pdf.cell(col_w[4], 6, origem[:10], border=0, align="C", fill=True)
        pdf.ln()

    pdf.ln(4)
    pdf.set_font("Arial", "I", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(0, 5, "Observação: os itens marcados como 'Exc-xx' representam excedentes além do limite de 38 vagas.")
    pdf.set_text_color(0, 0, 0)

    return pdf.output(dest="S").encode("latin-1")

# ==========================================================
# UI
# ==========================================================
st.set_page_config(page_title="Rota Nova Iguaçu", layout="centered")
st.markdown('<script src="https://telegram.org/js/telegram-web-app.js"></script>', unsafe_allow_html=True)

st.markdown("""
<style>
    .titulo-container { text-align: center; width: 100%; }
    .titulo-responsivo { font-size: clamp(1.2rem, 5vw, 2.2rem); font-weight: bold; margin-bottom: 6px; }
    .subtitulo-ciclo { text-align:center; font-size: 0.95rem; color: #444; margin-bottom: 16px; }
    .stCheckbox { background-color: #f8f9fa; padding: 5px; border-radius: 4px; border: 1px solid #eee; }
    .tabela-responsiva { width: 100%; overflow-x: auto; }
    table { width: 100% !important; font-size: 10px; table-layout: fixed; border-collapse: collapse; }
    th, td { text-align: center; padding: 2px !important; white-space: normal !important; word-wrap: break-word; }
    .footer { text-align: center; font-size: 11px; color: #888; margin-top: 40px; padding: 10px; border-top: 1px solid #eee; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="titulo-container"><div class="titulo-responsivo">🚌 ROTA NOVA IGUAÇU 🚌</div></div>', unsafe_allow_html=True)
ciclo_h, ciclo_d = obter_ciclo_atual()
st.markdown(f"<div class='subtitulo-ciclo'>Ciclo atual: <b>EMBARQUE {ciclo_h}h</b> do dia <b>{ciclo_d}</b></div>", unsafe_allow_html=True)

# session_state
if "usuario_logado" not in st.session_state:
    st.session_state.usuario_logado = None
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "conf_ativa" not in st.session_state:
    st.session_state.conf_ativa = False
if "_force_refresh_presenca" not in st.session_state:
    st.session_state._force_refresh_presenca = False
if "_adm_first_load" not in st.session_state:
    st.session_state._adm_first_load = False
if "_tel_login_fmt" not in st.session_state:
    st.session_state._tel_login_fmt = ""
if "_tel_cad_fmt" not in st.session_state:
    st.session_state._tel_cad_fmt = ""
if "_tel_rec_fmt" not in st.session_state:
    st.session_state._tel_rec_fmt = ""
if "_login_kind" not in st.session_state:
    st.session_state._login_kind = ""
if "_force_password_change" not in st.session_state:
    st.session_state._force_password_change = False
if "_force_profile_edit" not in st.session_state:
    st.session_state._force_profile_edit = False

def norm_str(x):
    return str(x or "").strip()

def email_basic_ok(e: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(e or "").strip()))

def user_to_ui_dict(u: dict) -> dict:
    """Normaliza o row do usuário para chaves que o app usa (e evita None no UI)."""
    if not u:
        return {}

    email = (_get_any(u, ["email", "Email", "E-mail", "E-mail cadastrado", "e_mail"], "") or "").strip().lower()
    nome = _get_any(u, ["nome", "nome_escala", "nomeEscala", "Nome de Escala", "Nome", "nome_de_escala"], "")
    telefone = _get_any(u, ["telefone", "tel", "Telefone", "phone"], "")
    graduacao = _get_any(u, ["graduacao", "Graduacao", "Graduação", "GRADUAÇÃO"], "")
    lotacao = _get_any(u, ["lotacao", "Lotacao", "Lotação", "LOTAÇÃO"], "")
    origem = _get_any(u, ["origem", "qg_rmcf_outros", "QG_RMCF_OUTROS", "QG"], "")
    status = (_get_any(u, ["status", "STATUS"], "") or "").strip().upper()

    # no app atual a senha é texto no BD (coluna 'senha')
    senha = _get_any(u, ["senha", "Senha", "SENHA"], "")

    ui = {
        # canônicas
        "id": _get_any(u, ["id", "ID"], None),
        "nome_escala": nome or email,
        "graduacao": graduacao,
        "lotacao": lotacao,
        "origem": origem,
        "senha": senha,
        "email": email,
        "status": status,
        "telefone": telefone,
        "last_recuperacao_dados_at": _get_any(u, ["last_recuperacao_dados_at", "Last_recuperacao_dados_at", "LAST_RECUPERACAO_DADOS_AT"], None),
    }

    # compatibilidade: chaves antigas usadas em algumas telas
    ui.update({
        "Nome": ui["nome_escala"],
        "Nome de Escala": ui["nome_escala"],
        "Email": ui["email"],
        "E-mail": ui["email"],
        "Telefone": ui["telefone"],
        "Graduação": ui["graduacao"],
        "Lotação": ui["lotacao"],
        "Origem": ui["origem"],
        "STATUS": ui["status"],
        "Senha": ui["senha"],
    })

    return ui
def _pick(row, *keys, default=None):
    """Retorna o primeiro valor não-vazio encontrado em 'row' para qualquer uma das chaves informadas.
    Também tenta variações em maiúsculo/minúsculo automaticamente.
    """
    if row is None:
        return default
    for k in keys:
        if k in row and row.get(k) not in (None, ""):
            return row.get(k)
        ku = k.upper()
        kl = k.lower()
        if ku in row and row.get(ku) not in (None, ""):
            return row.get(ku)
        if kl in row and row.get(kl) not in (None, ""):
            return row.get(kl)
    return default

def map_user_row(row: dict) -> dict:
    """Normaliza um registro vindo do Supabase para o formato UI (chaves com acento)."""
    if not isinstance(row, dict):
        return {}

    def pick(*keys, default=None):
        for k in keys:
            if k in row and row.get(k) is not None:
                return row.get(k)
        return default

    raw = {
        "id": pick("id", "ID"),
        "email": pick("email", "Email", "e_mail"),
        "telefone": pick("telefone", "Telefone", "tel", "celular"),
        "senha": pick("senha", "Senha", "password"),
        "nome_escala": pick("nome_escala", "nome", "Nome de Escala"),
        "graduacao": pick("graduacao", "Graduação", "grad"),
        "lotacao": pick("lotacao", "Lotação", "lot"),
        "origem": pick("origem", "Origem", "qg_rmcf_outros", "qg"),
        "status": pick("status", "STATUS", default="PENDENTE"),
        "created_at": pick("created_at"),
        "updated_at": pick("updated_at"),
        "last_recuperacao_dados_at": pick("last_recuperacao_dados_at"),
    }
    return user_to_ui_dict(raw)

def _get_any(d: dict, keys, default=None):
    """Retorna o primeiro valor não-nulo / não-vazio dentre as chaves informadas."""
    for k in keys:
        if not k:
            continue
        v = d.get(k, None) if isinstance(d, dict) else None
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        return v
    return default

def map_presenca_row(row: dict) -> dict:
    """Normaliza uma linha de 'presencas' para chaves usadas no app (inclusive legado)."""
    r = row or {}
    data_hora = _get_any(r, ["data_hora", "DATA_HORA", "dataHora", "created_at"], "")
    qg_rmcf_outros = _get_any(r, ["qg_rmcf_outros", "QG_RMCF_OUTROS", "origem"], "")
    graduacao = _get_any(r, ["graduacao", "GRADUAÇÃO", "graduacao_txt"], "")
    nome = _get_any(r, ["nome", "NOME", "nome_escala", "Nome de Escala", "Nome"], "")
    lotacao = _get_any(r, ["lotacao", "LOTAÇÃO", "lotacao_txt"], "")

    # Mantém o id do usuário para operações (confirmar/excluir)
    usuario_id = _get_any(r, ["usuario_id", "USUARIO_ID", "user_id"], None)
    presenca_id = _get_any(r, ["id", "ID"], None)

    out = {
        "id": presenca_id,
        "usuario_id": usuario_id,
        "data_hora": data_hora,
        "qg_rmcf_outros": qg_rmcf_outros,
        "graduacao": graduacao,
        "nome": nome,
        "lotacao": lotacao,

        # chaves legadas usadas em dataframes/telas
        "DATA_HORA": data_hora,
        "QG_RMCF_OUTROS": qg_rmcf_outros,
        "GRADUAÇÃO": graduacao,
        "NOME": nome,
        "LOTAÇÃO": lotacao,
    }
    return out

def _senha_temp_valida(u_dict):
    temp = str(u_dict.get("TEMP_SENHA", "") or "").strip()
    usada = u_dict.get("TEMP_USADA", None)
    exp = u_dict.get("TEMP_EXPIRA", "")

    # temp_usada pode vir bool, str, None
    if isinstance(usada, bool):
        usada_ok = (usada is False)
    else:
        usada_ok = (str(usada or "").strip().upper() in ["NAO", "NÃO", "FALSE", "0", ""])
    if not temp or not usada_ok:
        return False

    exp_dt = None
    if isinstance(exp, str) and exp.strip():
        exp_dt = _parse_dt(exp)
    else:
        # se vier timestamptz do postgres
        try:
            exp_dt_pd = pd.to_datetime(exp, errors="coerce")
            if pd.notna(exp_dt_pd):
                if exp_dt_pd.tzinfo is None:
                    exp_dt = FUSO_BR.localize(exp_dt_pd.to_pydatetime())
                else:
                    exp_dt = exp_dt_pd.tz_convert(FUSO_BR).to_pydatetime()
        except Exception:
            exp_dt = None

    if exp_dt is None:
        return False
    return _br_now() <= exp_dt

def _senha_confere(u_dict, senha_digitada: str):
    """Confere senha real e (opcional) senha temporária.
    Retorna (tipo, ok) onde tipo é 'REAL' ou 'TEMP'.
    """
    senha_digitada = str(senha_digitada or "").strip()

    # senha "real" (coluna atual)
    senha_real = str(u_dict.get("senha") or u_dict.get("Senha") or "").strip()
    if senha_real and senha_real == senha_digitada:
        return ("REAL", True)

    # senha temporária (se existir no schema)
    # aceita nomes antigos/novos para compatibilidade
    temp_senha = str(u_dict.get("temp_senha") or u_dict.get("TEMP_SENHA") or "").strip()
    if temp_senha and temp_senha == senha_digitada and _senha_temp_valida(u_dict):
        return ("TEMP", True)

    return ("", False)

def buscar_user_by_email_tel(email: str, tel_digits: str):
    email = str(email or "").strip().lower()
    tel_digits = tel_only_digits(tel_digits)
    data = usuarios_select({"email": email, "telefone": tel_digits})
    if not data:
        # alguns cadastros podem estar com telefone formatado; tenta comparar por "contém" (últimos 11)
        data_all = usuarios_select({"email": email})
        for u in data_all:
            if tel_only_digits(u.get("telefone", "")) == tel_digits:
                return u
        return None
    return data[0]

# ==========================================================
# APP
# ==========================================================
if "_edit_cadastro" not in st.session_state:
    st.session_state._edit_cadastro = False
if "_edit_user_id" not in st.session_state:
    st.session_state._edit_user_id = None

try:
    records_u_public_raw = buscar_usuarios_cadastrados()
    records_u_public = [user_to_ui_dict(u) for u in records_u_public_raw]
    limite_max = buscar_limite_dinamico()

    if st.session_state.usuario_logado is None and not st.session_state.is_admin:
        t1, t2, t3, t4, t5 = st.tabs(["Login", "Cadastro", "Instruções", "Recuperar", "ADM"])

        # -------------------------
        # LOGIN
        # -------------------------
        with t1:
            with st.form("form_login"):
                l_e = st.text_input("E-mail:")
                raw_tel_login = st.text_input("Telefone:", value=st.session_state._tel_login_fmt)
                fmt_tel_login = tel_format_br(raw_tel_login)
                st.session_state._tel_login_fmt = fmt_tel_login
                l_s = st.text_input("Senha:", type="password")

                entrou = st.form_submit_button("▶️ ENTRAR ◀️", use_container_width=True)
                if entrou:
                    if not tel_is_valid_11(fmt_tel_login):
                        st.error("Telefone inválido. Use DDD + 9 dígitos (ex: (21) 98765.4321).")
                    else:
                        tel_login_digits = tel_only_digits(fmt_tel_login)
                        email_login = l_e.strip().lower()

                        # busca usuário no DB
                        u_raw = buscar_user_by_email_tel(email_login, tel_login_digits)
                        u_a = user_to_ui_dict(u_raw) if u_raw else None

                        if u_a and _senha_confere(u_a, l_s)[1]:
                            status_user = str(u_a.get("STATUS", "")).strip().upper()
                            if status_user == "ATIVO":
                                kind, ok = _senha_confere(u_a, l_s)
                                st.session_state.usuario_logado = u_a
                                st.session_state._login_kind = kind

                                if kind == "TEMP":
                                    # marca como usada e força troca de senha + edição de cadastro (exceto e-mail)
                                    try:
                                        usuarios_update({"id": u_raw["id"]}, {"temp_usada": True})
                                        buscar_usuarios_cadastrados.clear()
                                        buscar_usuarios_admin.clear()
                                    except Exception:
                                        pass
                                    st.session_state._force_password_change = True
                                    st.session_state._force_profile_edit = True

                                st.rerun()
                            else:
                                st.error("Acesso negado. Aguardando aprovação do Administrador.")
                        else:
                            st.error("Dados incorretos.")

        # -------------------------
        # CADASTRO
        # -------------------------
        with t2:
            if len(records_u_public) >= limite_max:
                st.warning(f"⚠️ Limite de {limite_max} usuários atingido.")
            else:
                with st.form("form_novo_cadastro"):
                    n_n = st.text_input("Nome de Escala:")
                    n_e = st.text_input("E-mail:")

                    raw_tel_cad = st.text_input("Telefone:", value=st.session_state._tel_cad_fmt)
                    fmt_tel_cad = tel_format_br(raw_tel_cad)
                    st.session_state._tel_cad_fmt = fmt_tel_cad

                    n_g = st.selectbox("Graduação:", LISTA_GRAD)
                    n_l = st.text_input("Lotação:")
                    n_o = st.selectbox("Origem:", LISTA_ORIGEM)
                    n_p = st.text_input("Senha:", type="password")

                    cadastrou = st.form_submit_button("✍️ SALVAR CADASTRO 👈", use_container_width=True)
                    if cadastrou:
                        missing = []
                        if not norm_str(n_n): missing.append("Nome de Escala")
                        if not norm_str(n_e): missing.append("E-mail")
                        if norm_str(n_e) and not email_basic_ok(n_e): missing.append("E-mail (formato inválido)")
                        if not tel_is_valid_11(fmt_tel_cad): missing.append("Telefone (inválido)")
                        if not norm_str(n_g): missing.append("Graduação")
                        if not norm_str(n_l): missing.append("Lotação")
                        if not norm_str(n_o): missing.append("Origem")
                        if not norm_str(n_p): missing.append("Senha")

                        if missing:
                            st.error("Preencha corretamente todos os campos: " + ", ".join(missing) + ".")
                        else:
                            novo_email = norm_str(n_e).lower()
                            novo_tel_digits = tel_only_digits(fmt_tel_cad)

                            email_existe = any(str(u.get("Email", "")).strip().lower() == novo_email for u in records_u_public)
                            tel_existe = any(tel_only_digits(u.get("TELEFONE", "")) == novo_tel_digits for u in records_u_public)

                            if email_existe and tel_existe:
                                st.error("E-mail e Telefone já cadastrados.")
                            elif email_existe:
                                st.error("E-mail já cadastrado.")
                            elif tel_existe:
                                st.error("Telefone já cadastrado.")
                            else:
                                usuarios_insert({
                                    "nome": norm_str(n_n),
                                    "graduacao": norm_str(n_g),
                                    "lotacao": norm_str(n_l),
                                    "senha": norm_str(n_p),
                                    "origem": norm_str(n_o),
                                    "email": novo_email,
                                    "telefone": novo_tel_digits,
                                    "status": "PENDENTE",
                                    "temp_senha": "",
                                    "temp_expira": None,
                                    "temp_usada": True,
                                    "last_recuperacao_dados_at": None
                                })
                                buscar_usuarios_cadastrados.clear()
                                buscar_usuarios_admin.clear()
                                st.success("Cadastro realizado! Aguardando aprovação do Administrador.")
                                st.rerun()

        # -------------------------
        # INSTRUÇÕES
        # -------------------------
        with t3:
            st.markdown("### 📖 Guia de Uso")
            st.success("📲 **COMO INSTALAR (TELA INICIAL)**")
            st.markdown("**No Chrome (Android):** Toque nos 3 pontos (⋮) e em 'Instalar Aplicativo'.")
            st.markdown("**No Safari (iPhone):** Toque em Compartilhar (⬆️) e em 'Adicionar à Tela de Início'.")
            st.markdown("**No Telegram:** Procure o bot `@RotaNovaIguacuBot` e toque no botão 'Abrir App Rota' no menu.")
            st.divider()
            st.info("**CADASTRO E LOGIN:** Use seu e-mail como identificador único.")
            st.markdown("""
            **1. Regras de Horário:**
            * **Manhã:** Inscrições abertas até às 05:00h. Reabre às 07:00h.
            * **Tarde:** Inscrições abertas até às 17:00h. Reabre às 19:00h.
            * **Finais de Semana:** Abrem domingo às 19:00h.

            **2. Observação:**
            * Nos períodos em que a lista ficar suspensa para conferência (05:00h às 07:00h / 17:00h às 19:00h), os três PPMM que estiverem no topo da lista terão acesso à lista de check up (botão no topo da lista) para tirar a falta de quem estará entrando no ônibus. O mais antigo assume e na ausência dele o seu sucessor assume.
            * Após o horário de 06:50h e de 18:50h, a lista será automaticamente zerada para que o novo ciclo da lista possa ocorrer. Sendo assim, caso queira manter um histórico de viagem, antes desses horários, faça o download do pdf e/ou do resumo do W.Zap.
            """)

        # -------------------------
        # RECUPERAR (gera senha temp 1 acesso)
        # -------------------------
        with t4:
            st.markdown("### 🔐 Recuperar acesso")
            st.caption("Confirme **E-mail + Senha**.")

            e_r = st.text_input("E-mail cadastrado:")
            s_r = st.text_input("Senha do usuário:", type="password")

            c1, c2 = st.columns(2)
            with c1:
                btn_email = st.button("📧 Enviar dados para o Email cadastrado 📧", use_container_width=True)
            with c2:
                btn_edit = st.button("✏️ EDITAR CADASTRO ✏️", use_container_width=True)

            def _validar_email_senha():
                if not str(e_r or "").strip():
                    st.error("Informe o e-mail cadastrado.")
                    return None, None, None
                if not str(s_r or "").strip():
                    st.error("Informe a senha do usuário.")
                    return None, None, None
                uid, u_raw = buscar_user_by_email_senha(e_r, s_r)
                if not uid or not u_raw:
                    st.error("Dados não encontrados (verifique e-mail e senha).")
                    return None, None, None
                u_ui = map_user_row(u_raw)
                return uid, u_raw, u_ui

            # 1) MANTER botão e funcionalidade de envio por e-mail (agora validando por e-mail + senha)
            if btn_email:
                uid, u_raw, u_ui = _validar_email_senha()
                if uid and u_ui:
                    ok, prev_value, new_value, next_allowed = try_acquire_recuperacao_token(uid)
                    if not ok:
                        when_txt = next_allowed.strftime("%d/%m/%Y %H:%M") if next_allowed else "amanhã"
                        st.warning(f"⚠️ Você já recuperou seus dados hoje. Tente novamente {when_txt}.")
                    else:
                        try:
                            enviar_dados_cadastrais_para_email(u_ui)
                            st.success("✅ Dados enviados para o e-mail cadastrado.")
                        except Exception as ex:
                            # Se falhar o e-mail, desfaz o bloqueio do dia (rollback)
                            rollback_recuperacao_token(uid, prev_value)
                            st.error(f"Falha ao enviar e-mail: {ex}")

            # 2) EDITAR CADASTRO (substitui o 'Gerar senha temporária')
            if btn_edit:
                uid, u_raw, u_ui = _validar_email_senha()
                if uid and u_ui:
                    st.session_state._edit_cadastro = True
                    st.session_state._edit_user_id = uid
                    st.session_state._edit_user_ui = u_ui

            if st.session_state.get("_edit_cadastro", False):
                u_ui = st.session_state.get("_edit_user_ui") or {}
                st.divider()
                st.subheader("✏️ Editar cadastro (o e-mail não pode ser alterado)")

                # Opções fixas (ordem solicitada)
                grad_opcoes = ["TCEL", "MAJ", "CAP", "1º TEN", "2º TEN", "SUBTEN", "1º SGT", "2º SGT", "3º SGT", "CB", "SD", "FC COM", "FC TER"]
                origem_opcoes = ["QG", "RMCF", "OUTROS"]

                with st.form("form_editar_cadastro"):
                    nome_novo = st.text_input("Nome de Escala:", value=str(u_ui.get("Nome", "") or ""))
                    tel_novo_raw = st.text_input("Telefone:", value=tel_format_br(u_ui.get("TELEFONE", "") or ""))
                    tel_novo_fmt = tel_format_br(tel_novo_raw)

                    grad_atual = str(u_ui.get("Graduação", "") or "")
                    if grad_atual not in grad_opcoes:
                        grad_atual = grad_opcoes[0]
                    grad_nova = st.selectbox("Graduação:", grad_opcoes, index=grad_opcoes.index(grad_atual))

                    lot_nova = st.text_input("Lotação:", value=str(u_ui.get("Lotação", "") or ""))

                    orig_atual = str(u_ui.get("QG_RMCF_OUTROS", "") or "")
                    if orig_atual not in origem_opcoes:
                        orig_atual = origem_opcoes[0]
                    orig_nova = st.selectbox("Origem:", origem_opcoes, index=origem_opcoes.index(orig_atual))

                    st.caption("Se não quiser trocar a senha, deixe em branco.")
                    senha1 = st.text_input("Nova senha:", type="password")
                    senha2 = st.text_input("Confirmar nova senha:", type="password")

                    salvar = st.form_submit_button("💾 SALVAR ALTERAÇÕES", use_container_width=True)

                if salvar:
                    if not str(nome_novo or "").strip():
                        st.error("Informe o Nome de Escala.")
                    elif not tel_is_valid_11(tel_novo_fmt):
                        st.error("Telefone inválido. Use DDD + 9 dígitos (ex: (21) 98765.4321).")
                    elif not str(lot_nova or "").strip():
                        st.error("Informe a Lotação.")
                    elif (senha1 or senha2) and (senha1 != senha2):
                        st.error("As senhas não conferem.")
                    else:
                        try:
                            uid = st.session_state.get("_edit_user_id")
                            if not uid:
                                st.error("Não foi possível identificar o usuário para edição.")
                            else:
                                payload = {
                                    "nome": str(nome_novo).strip(),
                                    "telefone": tel_only_digits(tel_novo_fmt),
                                    "graduacao": str(grad_nova).strip(),
                                    "lotacao": str(lot_nova).strip(),
                                    "origem": str(orig_nova).strip(),
                                    # limpa temporária (garante que não fique pendência)
                                    "temp_senha": None,
                                    "temp_expira": None,
                                    "temp_usada": True,
                                }
                                if str(senha1 or "").strip():
                                    payload["senha"] = str(senha1)

                                usuarios_update({"id": uid}, payload)

                                st.success("✅ Cadastro atualizado.")
                                st.session_state._edit_cadastro = False
                                st.session_state._edit_user_id = None
                                st.session_state._edit_user_ui = None
                                st.rerun()
                        except Exception as ex:
                            st.error(f"Falha ao atualizar cadastro: {ex}")


        with t5:
            with st.form("form_admin"):
                ad_u = st.text_input("Usuário ADM:")
                ad_s = st.text_input("Senha ADM:", type="password")
                entrou_adm = st.form_submit_button("☠️ ACESSAR PAINEL ☠️")
                if entrou_adm:
                    if ad_u == "Administrador" and ad_s == "Administrador@123":
                        st.session_state.is_admin = True
                        st.session_state._adm_first_load = True
                        st.rerun()
                    else:
                        st.error("ADM inválido.")

    # =========================================
    # PAINEL ADM
    # =========================================
    elif st.session_state.is_admin:
        st.header("🛡️ PAINEL ADMINISTRATIVO 🛡️")

        sair_btn = st.button("⬅️ SAIR DO PAINEL")
        if sair_btn:
            st.session_state.is_admin = False
            st.session_state._adm_first_load = False
            st.rerun()

        if st.session_state._adm_first_load:
            buscar_usuarios_admin.clear()
            st.session_state._adm_first_load = False

        records_u_raw = buscar_usuarios_admin()
        records_u = [user_to_ui_dict(u) for u in records_u_raw]

        cA, cB = st.columns([1, 1])
        with cA:
            att_btn = st.button("🔄 Atualizar Usuários", use_container_width=True)
            if att_btn:
                buscar_usuarios_admin.clear()
                st.rerun()
        with cB:
            st.caption("ADM lê mais fresco (TTL=3s).")

        st.subheader("⚙️ Configurações Globais")
        novo_limite = st.number_input("Limite máximo de usuários:", value=int(limite_max))
        salvar_lim = st.button("💾 SALVAR NOVO LIMITE")
        if salvar_lim:
            config_set_int("limite_usuarios", int(novo_limite))
            st.success("Limite atualizado!")
            st.rerun()

        st.divider()
        st.subheader("👥 Gestão de Usuários")
        busca = st.text_input("🔍 Pesquisar por Nome ou E-mail:").strip().lower()

        ativar_all = st.button("✅ ATIVAR TODOS E DESLOGAR", use_container_width=True)
        if ativar_all and records_u_raw:
            for u in records_u_raw:
                usuarios_update({"id": u["id"]}, {"status": "ATIVO"})
            buscar_usuarios_admin.clear()
            buscar_usuarios_cadastrados.clear()
            st.session_state.clear()
            st.rerun()

        for i, user in enumerate(records_u):
            nome = user.get("Nome", "")
            email = user.get("Email", "")
            if busca == "" or busca in str(nome).lower() or busca in str(email).lower():
                status = str(user.get("STATUS", "")).upper()
                with st.expander(f"{user.get('Graduação')} {nome} - {status}"):
                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.write(f"📧 {email} | 📱 {user.get('TELEFONE')}")
                    is_ativo = (status == "ATIVO")

                    new_val = c2.checkbox("Liberar", value=is_ativo, key=f"adm_chk_{i}")
                    if new_val != is_ativo:
                        usuarios_update({"id": user["id"]}, {"status": "ATIVO" if new_val else "INATIVO"})
                        buscar_usuarios_admin.clear()
                        buscar_usuarios_cadastrados.clear()
                        st.rerun()

                    del_btn = c3.button("🗑️", key=f"del_{i}")
                    if del_btn:
                        usuarios_delete({"id": user["id"]})
                        buscar_usuarios_admin.clear()
                        buscar_usuarios_cadastrados.clear()
                        st.rerun()

    # =========================================
    # USUÁRIO LOGADO
    # =========================================
    else:
        u = st.session_state.usuario_logado

        # ------------------------------------------------------
        # Se entrou com senha temporária: forçar trocar senha + editar cadastro (exceto e-mail)
        # ------------------------------------------------------
        if st.session_state.get("_force_password_change", False) or st.session_state.get("_force_profile_edit", False):
            st.warning("🔐 Você entrou com uma **senha temporária**. Confirme seus dados e defina uma **nova senha** para concluir o acesso.")

            st.markdown("### ✅ Atualizar Cadastro (e-mail não pode mudar)")
            with st.form("form_update_profile_temp"):
                st.text_input("E-mail (fixo):", value=str(u.get("Email","")), disabled=True)
                nome_n = st.text_input("Nome de Escala:", value=str(u.get("Nome","")))
                grad_n = st.selectbox("Graduação:", LISTA_GRAD, index=max(0, LISTA_GRAD.index(str(u.get("Graduação","")))) if str(u.get("Graduação","")) in LISTA_GRAD else 0)
                lot_n = st.text_input("Lotação:", value=str(u.get("Lotação","")))
                orig_n = st.selectbox("Origem:", LISTA_ORIGEM, index=max(0, LISTA_ORIGEM.index(str(u.get("QG_RMCF_OUTROS","")))) if str(u.get("QG_RMCF_OUTROS","")) in LISTA_ORIGEM else 0)

                raw_tel = st.text_input("Telefone (DDD + 9 dígitos):", value=tel_format_br(u.get("TELEFONE","")))
                fmt_tel = tel_format_br(raw_tel)

                nova1 = st.text_input("Nova senha:", type="password")
                nova2 = st.text_input("Confirmar nova senha:", type="password")

                ok_btn = st.form_submit_button("💾 SALVAR E ENTRAR", use_container_width=True)

            if ok_btn:
                if not norm_str(nome_n):
                    st.error("Informe o Nome de Escala.")
                elif not norm_str(lot_n):
                    st.error("Informe a Lotação.")
                elif not tel_is_valid_11(fmt_tel):
                    st.error("Telefone inválido. Use DDD + 9 dígitos (ex: (21) 98765.4321).")
                elif not norm_str(nova1):
                    st.error("Informe a nova senha.")
                elif nova1 != nova2:
                    st.error("As senhas não conferem.")
                else:
                    try:
                        tel_digits = tel_only_digits(fmt_tel)
                        # evita colisão de telefone com outro usuário
                        outros = usuarios_select({"telefone": tel_digits})
                        outros = [o for o in outros if str(o.get("email","")).lower() != str(u.get("Email","")).lower()]
                        if outros:
                            st.error("Telefone já cadastrado por outro usuário.")
                        else:
                            u_raw = usuarios_select({"email": str(u.get("Email","")).lower()})
                            if not u_raw:
                                st.error("Não encontrei seu usuário no banco para atualizar.")
                            else:
                                uid = u_raw[0]["id"]
                                usuarios_update({"id": uid}, {
                                    "nome": norm_str(nome_n),
                                    "graduacao": norm_str(grad_n),
                                    "lotacao": norm_str(lot_n),
                                    "origem": norm_str(orig_n),
                                    "telefone": tel_digits,
                                    "senha": str(nova1),
                                    "temp_senha": "",
                                    "temp_expira": None,
                                    "temp_usada": True
                                })
                                buscar_usuarios_cadastrados.clear()
                                buscar_usuarios_admin.clear()

                                # atualiza sessão
                                u["Nome"] = norm_str(nome_n)
                                u["Graduação"] = norm_str(grad_n)
                                u["Lotação"] = norm_str(lot_n)
                                u["QG_RMCF_OUTROS"] = norm_str(orig_n)
                                u["TELEFONE"] = tel_digits
                                u["Senha"] = str(nova1)

                                st.session_state._force_password_change = False
                                st.session_state._force_profile_edit = False
                                st.session_state._login_kind = "REAL"
                                st.success("✅ Cadastro e senha atualizados. Acesso liberado.")
                                st.rerun()
                    except Exception as ex:
                        st.error(f"Falha ao atualizar: {ex}")

            st.stop()

        # Sidebar
        st.sidebar.markdown("### 👤 Usuário Conectado 🙍‍♂️")
        st.sidebar.info(f"**{u.get('Graduação')} {u.get('Nome')}**")
        sair_user = st.sidebar.button("⬅️ Sair", use_container_width=True)
        if sair_user:
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

        st.sidebar.markdown("---")
        st.sidebar.caption("Desenvolvido por: MAJ ANDRÉ AGUIAR - CAES®️")

        # Presença
        if st.session_state._force_refresh_presenca:
            buscar_presenca_atualizada.clear()
            st.session_state._force_refresh_presenca = False
        # Defaults (evita NameError se algo falhar no meio)
        presencas_raw = []
        aberto = False
        janela_conferencia = False
        usuario_id_logado = None
        ciclo_data = None
        ciclo_data_iso = None
        ciclo_hora = None
        turno_atual = None

        presencas_raw = buscar_presenca_atualizada()
        # === Ciclo atual (data/hora) ===
        ciclo_hora, ciclo_data_br = obter_ciclo_atual()
        try:
            ciclo_data_iso = ciclo_data_br.strftime("%Y-%m-%d")
        except Exception:
            ciclo_data_iso = str(ciclo_data_br)

        ciclo_key = f"{ciclo_data_iso}_{ciclo_hora}"

        # Limpa automaticamente presenças de ciclos anteriores (1x por ciclo por sessão)
        if st.session_state.get("_ciclo_limpeza_key") != ciclo_key:
            try:
                limpar_presencas_de_outros_ciclos(ciclo_hora, ciclo_data_iso)
            finally:
                st.session_state["_ciclo_limpeza_key"] = ciclo_key
            presencas_raw = buscar_presenca_atualizada()

        # Define se está aberto para novas confirmações + limpeza por marco (06:50/18:50)
        aberto, janela_conferencia = verificar_status_e_limpar_db(presencas_raw)

        if not aberto:
            st.info("⏳ Lista fechada para novas inscrições.")

        # Dados do usuário logado
        u_log = user_to_ui_dict(st.session_state.get("usuario_logado") or {})
        usuario_id_logado = u_log.get("id")
        nome_logado = u_log.get("nome_escala") or ""
        graduacao_logado = u_log.get("graduacao") or ""
        lotacao_logado = u_log.get("lotacao") or ""
        origem_logado = u_log.get("origem") or "QG"
        email_logado = u_log.get("email") or ""
        telefone_logado = u_log.get("telefone") or ""

        def _ja_confirmou_no_ciclo() -> bool:
            if not usuario_id_logado:
                return False
            for rr in presencas_raw or []:
                try:
                    if rr.get("usuario_id") != usuario_id_logado:
                        continue
                    if str(rr.get("ciclo_hora")) != str(ciclo_hora):
                        continue
                    if str(rr.get("ciclo_data")) != str(ciclo_data_iso):
                        continue
                    return True
                except Exception:
                    pass
            return False

        ja_confirmou = _ja_confirmou_no_ciclo()

        # Ação principal: confirmar / excluir
        if ja_confirmou:
            st.warning("⚠️ Você já confirmou sua presença neste ciclo.")
            if st.button("🚫 EXCLUIR MINHA PRESENÇA ⚠️", use_container_width=True, key="btn_excluir_minha_presenca"):
                try:
                    if not usuario_id_logado:
                        raise Exception("Usuário não identificado (sessão). Faça login novamente.")
                    supabase.table("presencas").delete() \
                        .eq("usuario_id", usuario_id_logado) \
                        .eq("ciclo_hora", str(ciclo_hora)) \
                        .eq("ciclo_data", str(ciclo_data_iso)) \
                        .execute()
                    st.success("✅ Presença excluída.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Falha ao excluir presença: {e}")
        else:
            if aberto:
                if st.button("🚀 CONFIRMAR MINHA PRESENÇA ✅", use_container_width=True, key="btn_confirmar_minha_presenca"):
                    try:
                        if not usuario_id_logado:
                            raise Exception("Usuário não identificado (sessão). Faça login novamente.")
                        payload = {
                            "usuario_id": usuario_id_logado,
                            "nome": nome_logado,
                            "graduacao": graduacao_logado,
                            "lotacao": lotacao_logado,
                            "qg_rmcf_outros": origem_logado,
                            "email": email_logado,
                            "telefone": telefone_logado,
                            "ciclo_hora": str(ciclo_hora),
                            "ciclo_data": str(ciclo_data_iso),
                        }
                        supabase.table("presencas").insert(payload).execute()
                        st.success("✅ Presença confirmada!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Falha ao confirmar presença: {e}")
            else:
                if janela_conferencia:
                    st.warning(f"⏳ Lista fechada. {janela_conferencia}")
                else:
                    st.warning("⏳ Lista fechada no momento.")
            with c_up1:
                up_btn = st.button("🔄 ATUALIZAR", use_container_width=True, key="btn_atualizar_tabela")
                if up_btn:
                    st.rerun()
                    st.rerun()
            with c_up2:
                st.caption("Atualiza sob demanda.")

            st.write(
                f"<div class='tabela-responsiva'>{df_v.drop(columns=['EMAIL']).to_html(index=False, justify='center', border=0, escape=False)}</div>",
                unsafe_allow_html=True
            )

            c1, c2 = st.columns(2)
            with c1:
                insc = int(df_o.shape[0]) if df_o is not None else 0
                resumo = {"inscritos": insc, "vagas": 38}
                pdf_bytes = gerar_pdf_apresentado(df_o, resumo)
                _ = st.download_button(
                    "📄 PDF (Relatório)",
                    pdf_bytes,
                    "lista_rota_nova_iguacu.pdf",
                    use_container_width=True
                )

            with c2:
                txt_w = "*🚌 LISTA DE PRESENÇA*\n\n"
                for _, r in df_o.iterrows():
                    txt_w += f"{r['Nº']}. {r['GRADUAÇÃO']} {r['NOME']}\n"
                st.markdown(
                    f'<a href="https://wa.me/?text={urllib.parse.quote(txt_w)}" target="_blank">'
                    f"<button style='width:100%; height:38px; background-color:#25D366; color:white; border:none; "
                    f"border-radius:4px; font-weight:bold;'>🟢 WHATSAPP</button></a>",
                    unsafe_allow_html=True
                )

    st.markdown('<div class="footer">Desenvolvido por: <b>MAJ ANDRÉ AGUIAR - CAES®️</b></div>', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div style="width:100%; text-align:center; margin-top:12px;">
            <img src="{GIF_URL}" style="width:80%; max-width:520px; height:auto;" />
        </div>
        """,
        unsafe_allow_html=True
    )

except Exception as e:
    st.error(f"⚠️ Erro: {e}")
