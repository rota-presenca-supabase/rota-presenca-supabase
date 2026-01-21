import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
import secrets
import re
import bcrypt
from supabase import create_client, Client
from fpdf import FPDF

# =========================
# SUPABASE CONFIG
# =========================
# Coloque estes valores em .streamlit/secrets.toml:
# SUPABASE_URL = "https://xxxx.supabase.co"
# SUPABASE_SERVICE_ROLE_KEY = "xxxxx"   # RECOMENDADO (server-side no Streamlit Cloud)
#
# Opcional:
# APP_TZ = "America/Sao_Paulo"
# LIMITE_MAX_USUARIOS = 500
#
# IMPORTANTE:
# - Não use a chave anon para administrar usuário/senha. Use SERVICE ROLE.
# - Se você já criou RLS muito permissiva (true para public), remova/ajuste depois.
# =========================

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# -------------------------
# Helpers
# -------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _bcrypt_hash(plain: str) -> str:
    pw = plain.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pw, salt).decode("utf-8")

def _bcrypt_check(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def _clean(s: str) -> str:
    return (s or "").strip()

def _require_secret(key: str) -> str:
    v = st.secrets.get(key, "")
    if not v:
        st.error(f"Faltou configurar `{key}` em secrets.toml.")
        st.stop()
    return v

@st.cache_resource(show_spinner=False)
def supabase_client() -> Client:
    url = _require_secret("SUPABASE_URL")
    key = _require_secret("SUPABASE_SERVICE_ROLE_KEY")
    return create_client(url, key)

# -------------------------
# DB helpers
# -------------------------
def db_get_usuario_by_email(email: str):
    sb = supabase_client()
    resp = sb.table("usuarios").select("*").eq("email", email).limit(1).execute()
    return resp.data[0] if resp.data else None

def db_insert_usuario(payload: dict):
    sb = supabase_client()
    return sb.table("usuarios").insert(payload).execute()

def db_update_usuario_by_id(user_id: str, payload: dict):
    sb = supabase_client()
    return sb.table("usuarios").update(payload).eq("id", user_id).execute()

def db_list_usuarios(limit: int = 500):
    sb = supabase_client()
    resp = sb.table("usuarios").select("id,created_at,nome,email,telefone,graduacao,lotacao,origem,status").order("created_at", desc=True).limit(limit).execute()
    return resp.data or []

def db_insert_presenca(payload: dict):
    sb = supabase_client()
    return sb.table("presencas").insert(payload).execute()

def db_list_presencas(limit: int = 1000):
    sb = supabase_client()
    resp = sb.table("presencas").select("*").order("created_at", desc=True).limit(limit).execute()
    return resp.data or []

def db_list_presencas_por_usuario(usuario_id: str, limit: int = 200):
    sb = supabase_client()
    resp = sb.table("presencas").select("*").eq("usuario_id", usuario_id).order("created_at", desc=True).limit(limit).execute()
    return resp.data or []

# =========================
# RESET / RECUPERAÇÃO
# =========================
# Para o "Recuperar" funcionar como você quer (editar cadastro completo sem trocar email),
# adicione estas colunas em public.usuarios (uma vez só):
#
# alter table public.usuarios
#   add column if not exists reset_token text,
#   add column if not exists reset_expires_at timestamptz;
#
# Observação: se você preferir, isso pode virar uma tabela separada (reset_tokens).
# =========================
def db_set_reset_token(user_id: str, token: str, expires_at: datetime):
    db_update_usuario_by_id(user_id, {
        "reset_token": token,
        "reset_expires_at": expires_at.isoformat(),
    })

def db_clear_reset_token(user_id: str):
    db_update_usuario_by_id(user_id, {
        "reset_token": None,
        "reset_expires_at": None,
    })

def reset_token_valido(usuario_row: dict, token: str) -> bool:
    if not usuario_row:
        return False
    if (usuario_row.get("reset_token") or "") != token:
        return False
    exp = usuario_row.get("reset_expires_at") or usuario_row.get("reset_expires_at".upper())
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except Exception:
        return False
    return _now_utc() <= exp_dt

# =========================
# UI
# =========================
st.set_page_config(page_title="Rota Presença", layout="wide")

# Listas fixas (ordem solicitada)
GRADUACOES = ["TCEL", "MAJ", "CAP", "1º TEN", "2º TEN", "SUBTEN", "1º SGT", "2º SGT", "3º SGT", "CB", "SD", "FC COM", "FC TER"]
ORIGENS = ["QG", "RMCF", "OUTROS"]


if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

def ui_header():
    st.title("Rota Presença (Supabase)")
    st.caption("Banco: Supabase Postgres • Streamlit")

def ui_login():
    ui_header()
    st.subheader("Login")

    col1, col2 = st.columns(2)
    with col1:
        email = _clean(st.text_input("Email", key="login_email"))
    with col2:
        senha = st.text_input("Senha", type="password", key="login_senha")

    if st.button("Entrar", type="primary"):
        if not EMAIL_RE.match(email):
            st.error("Email inválido.")
            return
        if not senha:
            st.error("Informe a senha.")
            return

        user = db_get_usuario_by_email(email)
        if not user:
            st.error("Usuário não encontrado.")
            return

        # Campo 'senha' guarda hash bcrypt
        if not _bcrypt_check(senha, user.get("senha") or ""):
            st.error("Senha incorreta.")
            return

        if (user.get("status") or "").upper() == "BLOQUEADO":
            st.error("Usuário bloqueado.")
            return

        st.session_state.auth_user = user
        st.success("Logado.")
        st.rerun()

    st.divider()
    st.info("Se você ainda não tem cadastro, use o menu **Cadastro**. Para recuperar/editar cadastro, use **Recuperar**.")

def ui_cadastro():
    ui_header()
    st.subheader("Cadastro")

    c1, c2 = st.columns(2)
    with c1:
        nome = _clean(st.text_input("Nome de Escala"))
        email = _clean(st.text_input("Email"))
        telefone = _clean(st.text_input("Telefone"))
    with c2:
        graduacao = _clean(st.text_input("Graduação"))
        lotacao = _clean(st.text_input("Lotação"))
        origem = _clean(st.text_input("Origem"))

    s1, s2 = st.columns(2)
    with s1:
        senha1 = st.text_input("Senha", type="password")
    with s2:
        senha2 = st.text_input("Confirmar senha", type="password")

    if st.button("Criar cadastro", type="primary"):
        if not nome:
            st.error("Informe o nome.")
            return
        if not EMAIL_RE.match(email):
            st.error("Email inválido.")
            return
        if not telefone:
            st.error("Informe o telefone.")
            return
        if not senha1 or len(senha1) < 6:
            st.error("Senha precisa ter pelo menos 6 caracteres.")
            return
        if senha1 != senha2:
            st.error("As senhas não conferem.")
            return

        if db_get_usuario_by_email(email):
            st.error("Já existe cadastro com esse email.")
            return

        payload = {
            "nome": nome,
            "email": email,
            "telefone": telefone,
            "graduacao": graduacao,
            "lotacao": lotacao,
            "origem": origem,
            "senha": _bcrypt_hash(senha1),
            "status": "PENDENTE",
        }
        db_insert_usuario(payload)
        st.success("Cadastro criado (status PENDENTE).")
        st.info("Se você usa aprovação manual, vá em **Admin** para mudar o status para ATIVO.")

def ui_recuperar_editar():
    ui_header()
    st.subheader("Recuperar / Editar cadastro (sem trocar email)")

    st.caption("Fluxo: 1) Valida email+telefone 2) Gera token (10 min) 3) Com token, edita dados e/ou troca senha.")

    step = st.radio("Etapa", ["1) Gerar token", "2) Usar token e editar"], horizontal=True)

    if step.startswith("1"):
        email = _clean(st.text_input("Email cadastrado"))
        telefone = _clean(st.text_input("Telefone cadastrado"))

        if st.button("Gerar token (10 min)", type="primary"):
            if not EMAIL_RE.match(email):
                st.error("Email inválido.")
                return
            user = db_get_usuario_by_email(email)
            if not user:
                st.error("Usuário não encontrado.")
                return
            if _clean(user.get("telefone")) != telefone:
                st.error("Telefone não confere.")
                return

            token = secrets.token_urlsafe(8)  # curto o suficiente para digitar
            expires = _now_utc() + timedelta(minutes=10)
            db_set_reset_token(user["id"], token, expires)

            st.success("Token gerado.")
            st.warning("Como você ainda não configurou envio por email, copie o token abaixo e guarde:")
            st.code(token)
            st.caption(f"Válido até: {expires.isoformat()}")

    else:
        email = _clean(st.text_input("Email (não pode mudar)", key="edit_email"))
        token = _clean(st.text_input("Token temporário", key="edit_token"))

        if not EMAIL_RE.match(email) or not token:
            st.info("Preencha email e token.")
            return

        user = db_get_usuario_by_email(email)
        if not user:
            st.error("Usuário não encontrado.")
            return

        if not reset_token_valido(user, token):
            st.error("Token inválido ou expirado.")
            return

        st.success("Token válido. Pode editar.")
        st.divider()

        c1, c2 = st.columns(2)
        with c1:
            nome = _clean(st.text_input("Nome", value=user.get("nome") or ""))
            telefone = _clean(st.text_input("Telefone", value=user.get("telefone") or ""))
            graduacao = _clean(st.text_input("Graduação", value=user.get("graduacao") or ""))
        with c2:
            lotacao = _clean(st.text_input("Lotação", value=user.get("lotacao") or ""))
            origem = _clean(st.text_input("Origem", value=user.get("origem") or ""))
            status = _clean(st.text_input("Status", value=user.get("status") or "PENDENTE"))

        st.caption("Email é travado (não muda).")

        st.markdown("**Trocar senha (opcional)**")
        ns1, ns2 = st.columns(2)
        with ns1:
            nova1 = st.text_input("Nova senha", type="password", key="nova1")
        with ns2:
            nova2 = st.text_input("Confirmar nova senha", type="password", key="nova2")

        if st.button("Salvar alterações", type="primary"):
            payload = {
                "nome": nome,
                "telefone": telefone,
                "graduacao": graduacao,
                "lotacao": lotacao,
                "origem": origem,
                "status": status or user.get("status") or "PENDENTE",
            }

            if nova1 or nova2:
                if len(nova1) < 6:
                    st.error("Senha precisa ter pelo menos 6 caracteres.")
                    return
                if nova1 != nova2:
                    st.error("As senhas não conferem.")
                    return
                payload["senha"] = _bcrypt_hash(nova1)

            db_update_usuario_by_id(user["id"], payload)
            db_clear_reset_token(user["id"])
            st.success("Cadastro atualizado e token invalidado.")
            st.rerun()

def ui_registrar_presenca():
    ui_header()
    st.subheader("Registrar presença")

    user = st.session_state.auth_user
    if not user:
        st.warning("Faça login primeiro.")
        return

    st.write(f"Logado como: **{user.get('nome')}** ({user.get('email')})")

    if st.button("Registrar presença agora", type="primary"):
        payload = {
            "usuario_id": user["id"],
            "nome": user.get("nome") or "",
            "graduacao": user.get("graduacao") or "",
            "lotacao": user.get("lotacao") or "",
            "origem": user.get("origem") or "",
        }
        db_insert_presenca(payload)
        st.success("Presença registrada.")

    st.divider()
    st.subheader("Minhas presenças (últimas 200)")
    rows = db_list_presencas_por_usuario(user["id"], limit=200)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma presença registrada ainda.")

def gerar_pdf_presencas(rows: list[dict]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_font("Arial", size=12)

    pdf.cell(0, 8, "Relatorio de Presencas", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 6, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", ln=True)
    pdf.ln(4)

    headers = ["created_at", "nome", "graduacao", "lotacao", "origem", "usuario_id"]
    for h in headers:
        pdf.cell(32, 6, h[:10], border=1)
    pdf.ln()

    for r in rows:
        for h in headers:
            v = str(r.get(h, ""))[:20]
            pdf.cell(32, 6, v, border=1)
        pdf.ln()

    return pdf.output(dest="S").encode("latin-1")

def ui_admin():
    ui_header()
    st.subheader("Admin")

    st.warning("Esta área assume que o Streamlit está usando SUPABASE_SERVICE_ROLE_KEY. Não exponha isso no front-end.")

    # Limite de listagem (opcional)
    limite = int(st.secrets.get("LIMITE_MAX_USUARIOS", 500))

    tab1, tab2 = st.tabs(["Usuários", "Presenças"])

    with tab1:
        st.caption(f"Mostrando até {limite} usuários.")
        users = db_list_usuarios(limit=limite)
        if users:
            dfu = pd.DataFrame(users)
            st.dataframe(dfu, use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("### Alterar status de um usuário")
            email = _clean(st.text_input("Email do usuário", key="admin_email"))
            novo_status = st.selectbox("Novo status", ["ATIVO", "PENDENTE", "BLOQUEADO"], index=0)

            if st.button("Aplicar status", type="primary"):
                u = db_get_usuario_by_email(email)
                if not u:
                    st.error("Usuário não encontrado.")
                else:
                    db_update_usuario_by_id(u["id"], {"status": novo_status})
                    st.success("Status atualizado.")
                    st.rerun()
        else:
            st.info("Nenhum usuário ainda.")

    with tab2:
        pres = db_list_presencas(limit=1000)
        if pres:
            dfp = pd.DataFrame(pres)
            st.dataframe(dfp, use_container_width=True, hide_index=True)

            pdf_bytes = gerar_pdf_presencas(pres)
            st.download_button("Baixar PDF (presenças)", data=pdf_bytes, file_name="presencas.pdf", mime="application/pdf")
        else:
            st.info("Nenhuma presença registrada ainda.")

def ui_logout():
    st.session_state.auth_user = None
    st.success("Logout efetuado.")
    st.rerun()

# =========================
# ROUTER
# =========================
with st.sidebar:
    st.header("Menu")
    if st.session_state.auth_user:
        st.write(f"**{st.session_state.auth_user.get('nome','')}**")
        st.caption(st.session_state.auth_user.get("email", ""))

    opcoes = ["Login", "Cadastro", "Recuperar", "Registrar Presença", "Admin"]
    if st.session_state.auth_user:
        opcoes.append("Sair")

    page = st.radio("Ir para:", opcoes)

if page == "Login":
    ui_login()
elif page == "Cadastro":
    ui_cadastro()
elif page == "Recuperar":
    ui_recuperar_editar()
elif page == "Registrar Presença":
    ui_registrar_presenca()
elif page == "Admin":
    ui_admin()
elif page == "Sair":
    ui_logout()
