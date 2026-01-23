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
        r = supabase_admin.table("usuarios").select("last_recuperacao_dados_at").eq("id", user_id).limit(1).execute()
        if r.data and isinstance(r.data, list) and len(r.data) > 0:
            prev = r.data[0].get("last_recuperacao_dados_at")
    except Exception:
        prev = None

    # UPDATE condicional (só atualiza se ainda não recuperou hoje)
    cond = f"last_recuperacao_dados_at.is.null,last_recuperacao_dados_at.lt.{start_day_utc.isoformat()}"
    try:
        upd = (
            supabase_admin
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
        supabase_admin.table("usuarios").update({"last_recuperacao_dados_at": prev_value}).eq("id", user_id).execute()
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
        return usuarios_select()
    except Exception:
        return []

@st.cache_data(ttl=3)
def buscar_usuarios_admin():
    # Busca todos os campos para evitar incompatibilidade de schema
    try:
        return usuarios_select(columns="*")
    except Exception:
        return []

@st.cache_data(ttl=120)
def buscar_limite_dinamico():
    return config_get_int("limite_usuarios", 100)

@st.cache_data(ttl=6)
def buscar_presenca_atualizada():
    try:
        return presenca_select()
    except Exception:
        return []

# ==========================================================
# PRESENÇA: limpeza por ciclo (equivalente ao resize do Sheets)
# ==========================================================
def verificar_status_e_limpar_db(presencas_rows):
    agora = _br_now()
    hora_atual, dia_semana = agora.time(), agora.weekday()

    if hora_atual >= time(18, 50):
        marco = agora.replace(hour=18, minute=50, second=0, microsecond=0)
    elif hora_atual >= time(6, 50):
        marco = agora.replace(hour=6, minute=50, second=0, microsecond=0)
    else:
        marco = (agora - timedelta(days=1)).replace(hour=18, minute=50, second=0, microsecond=0)

    # se a última presença for anterior ao marco, zera tabela
    if presencas_rows:
        try:
            # pega a mais recente (comparando por datetime, não por string)
            from dateutil import parser as _dtparser
            dts = []
            for r in presencas_rows or []:
                dh = r.get("data_hora") if isinstance(r, dict) else None
                if not dh:
                    continue
                try:
                    dts.append(_dtparser.isoparse(dh))
                except Exception:
                    try:
                        dt_tmp = pd.to_datetime(dh, errors="coerce")
                        if pd.notna(dt_tmp):
                            dts.append(dt_tmp.to_pydatetime())
                    except Exception:
                        pass

            if dts:
                last_dt = max(dts)
                if getattr(last_dt, "tzinfo", None) is None:
                    last_dt = pytz.UTC.localize(last_dt)
                last_dt_br = last_dt.astimezone(tz)
            else:
                last_dt_br = None

            if last_dt_br and last_dt_br < marco:
                supabase.table("presencas").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
                st.session_state["_presencas_limpo_em"] = datetime.now(tz)
                st.rerun()
        except Exception:
            pass
    # Regras de abertura/fechamento (idênticas)
    if dia_semana == 5:  # Sábado
        is_aberto = False
    elif dia_semana == 6:  # Domingo
        is_aberto = (hora_atual >= time(19, 0))
    elif dia_semana == 4:  # Sexta
        if hora_atual >= time(17, 0):
            is_aberto = False
        elif time(5, 0) <= hora_atual < time(7, 0):
            is_aberto = False
        else:
            is_aberto = True
    else:  # Segunda a Quinta
        if (time(5, 0) <= hora_atual < time(7, 0)) or (time(17, 0) <= hora_atual < time(19, 0)):
            is_aberto = False
        else:
            is_aberto = True

    janela_conferencia = (time(5, 0) < hora_atual < time(7, 0)) or (time(17, 0) < hora_atual < time(19, 0))
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
    """
    Normaliza o registro do usuário vindo do Supabase para um dicionário usado na UI.
    Suporta esquemas antigos/novos (nome vs nome_escala, is_admin vs admin, aprovado vs status/ativo).
    """
    if not isinstance(u, dict):
        u = {}

    # Campos básicos (com fallback)
    user_id = u.get("id") or u.get("usuario_id") or u.get("user_id")
    email = u.get("email") or u.get("e_mail") or u.get("Email") or u.get("EMAIL")
    telefone = u.get("telefone") or u.get("phone") or u.get("celular") or u.get("Telefone")
    nome = u.get("nome_escala") or u.get("nome") or u.get("Nome") or u.get("NOME")
    graduacao = u.get("graduacao") or u.get("patente") or u.get("Graduacao")
    lotacao = u.get("lotacao") or u.get("unidade") or u.get("Lotacao")
    origem = u.get("origem") or u.get("setor") or u.get("Origem")

    # Admin / aprovação (com fallback)
    is_admin = bool(u.get("is_admin") if u.get("is_admin") is not None else u.get("admin") or u.get("isAdmin") or u.get("ADMIN"))
    aprovado_raw = u.get("aprovado")
    if aprovado_raw is None:
        # Alguns esquemas podem usar 'status'/'STATUS'/'ativo'
        status = (u.get("status") or u.get("STATUS") or "").strip().upper()
        ativo = u.get("ativo")
        if isinstance(ativo, bool):
            aprovado = ativo
        elif status in ("ATIVO", "APROVADO", "LIBERADO", "OK", "SIM", "TRUE"):
            aprovado = True
        elif status in ("PENDENTE", "BLOQUEADO", "INATIVO", "NAO", "NÃO", "FALSE"):
            aprovado = False
        else:
            # Se não existir campo, assume aprovado (evita travar login por schema)
            aprovado = True
    else:
        aprovado = bool(aprovado_raw)

    STATUS = "ATIVO" if aprovado else "PENDENTE"

    # Timestamp do limite de recuperação
    last_rec = u.get("last_recuperacao_dados_at") or u.get("ultima_recuperacao_dados_at")

    return {
        "id": user_id,
        "email": email,
        "telefone": telefone,
        "nome_escala": nome,
        "graduacao": graduacao,
        "lotacao": lotacao,
        "origem": origem,
        "is_admin": is_admin,
        "aprovado": aprovado,
        "STATUS": STATUS,
        "last_recuperacao_dados_at": last_rec,
    }
