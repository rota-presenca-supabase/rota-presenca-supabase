import streamlit as st
from supabase import create_client
from datetime import datetime, timedelta, timezone
import random
import re

# ==========================================================
# Listas fixas (conforme pedido)
# ==========================================================
GRADUACOES = [
    "TCEL", "MAJ", "CAP", "1º TEN", "2º TEN", "SUBTEN",
    "1º SGT", "2º SGT", "3º SGT", "CB", "SD", "FC COM", "FC TER",
]
ORIGENS = ["QG", "RMCF", "OUTROS"]

# ==========================================================
# Helpers
# ==========================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def gerar_senha_temporaria(length: int = 8) -> str:
    # similar ao estilo do app antigo: letras+numeros
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def require_supabase():
    """Cria o client do Supabase.

    IMPORTANTe: este app foi pensado para rodar com SERVICE ROLE (server-side),
    porque você usa uma tabela própria de usuários (não Supabase Auth).
    """
    url = st.secrets.get("SUPABASE_URL", "").strip()
    anon = st.secrets.get("SUPABASE_KEY", "").strip()
    service = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url:
        st.error("Faltou configurar SUPABASE_URL em secrets.")
        st.stop()

    # Prioriza SERVICE ROLE (necessário para bypass RLS com este design)
    key = service or anon
    if not key:
        st.error(
            "Faltou configurar SUPABASE_SERVICE_ROLE_KEY (ou ao menos SUPABASE_KEY) em secrets."
        )
        st.stop()

    return create_client(url, key)


# ==========================================================
# Banco (Supabase)
# ==========================================================

def db_get_usuario_by_email(sb, email: str):
    email = normalize_email(email)
    resp = sb.table("usuarios").select("*").eq("email", email).limit(1).execute()
    data = resp.data or []
    return data[0] if data else None


def db_create_usuario(sb, payload: dict):
    return sb.table("usuarios").insert(payload).execute()


def db_update_usuario_by_email(sb, email: str, updates: dict):
    email = normalize_email(email)
    return sb.table("usuarios").update(updates).eq("email", email).execute()


def db_list_usuarios(sb, limit: int = 500):
    return sb.table("usuarios").select("*").order("created_at", desc=True).limit(limit).execute().data or []


def db_insert_presenca(sb, payload: dict):
    return sb.table("presencas").insert(payload).execute()


def db_list_presencas(sb, limit: int = 500):
    return sb.table("presencas").select("*").order("created_at", desc=True).limit(limit).execute().data or []


# ==========================================================
# Sessão
# ==========================================================

def is_logged_in() -> bool:
    return bool(st.session_state.get("usuario"))


def logout():
    st.session_state["usuario"] = None
    st.session_state["forcar_alteracao"] = False


# ==========================================================
# UI - Páginas
# ==========================================================

def pagina_login(sb):
    st.header("Login")

    email = st.text_input("Email")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar", type="primary"):
        email_n = normalize_email(email)
        user = db_get_usuario_by_email(sb, email_n)

        if not user:
            st.error("Usuário não encontrado.")
            return

        if (user.get("senha") or "") != (senha or ""):
            st.error("Senha incorreta.")
            return

        # Se foi senha temporária, força alteração
        if user.get("senha_temporaria") and (senha or "") == (user.get("senha_temporaria") or ""):
            # valida expiração
            exp_raw = user.get("senha_temporaria_expira")
            if not exp_raw:
                st.error("Senha temporária inválida. Gere outra no menu Recuperar.")
                return

            # supabase retorna ISO str
            exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
            if now_utc() > exp_dt:
                st.error("Senha temporária expirada. Gere outra no menu Recuperar.")
                return

            st.session_state["usuario"] = user
            st.session_state["forcar_alteracao"] = True
            st.success("Login com senha temporária. Agora atualize seus dados.")
            return

        # login normal
        st.session_state["usuario"] = user
        st.session_state["forcar_alteracao"] = False
        st.success("Login realizado.")


def pagina_cadastro(sb):
    st.header("Cadastro")

    # Ajustes pedidos:
    # - Nome completo -> Nome de Escala
    # - Graduação e Origem como selectbox

    nome = st.text_input("Nome de Escala")
    email = st.text_input("Email")
    telefone = st.text_input("Telefone")

    col1, col2 = st.columns(2)
    with col1:
        graduacao = st.selectbox("Graduação", GRADUACOES, index=1)  # MAJ por padrão
        lotacao = st.text_input("Lotação")
    with col2:
        origem = st.selectbox("Origem", ORIGENS, index=0)

    senha = st.text_input("Senha", type="password")
    senha2 = st.text_input("Confirmar senha", type="password")

    if st.button("Criar cadastro", type="primary"):
        email_n = normalize_email(email)
        tel_n = only_digits(telefone)

        if not nome.strip():
            st.error("Informe o Nome de Escala.")
            return
        if not email_n or "@" not in email_n:
            st.error("Informe um email válido.")
            return
        if len(tel_n) < 8:
            st.error("Informe um telefone válido.")
            return
        if not senha:
            st.error("Informe uma senha.")
            return
        if senha != senha2:
            st.error("As senhas não conferem.")
            return

        if db_get_usuario_by_email(sb, email_n):
            st.error("Já existe usuário com esse email.")
            return

        payload = {
            "nome": nome.strip(),
            "email": email_n,
            "telefone": tel_n,
            "graduacao": graduacao,
            "lotacao": lotacao.strip() or "-",
            "origem": origem,
            "senha": senha,
            "status": "PENDENTE",
            # campos de recuperação (opcionais)
            "senha_temporaria": None,
            "senha_temporaria_expira": None,
        }

        try:
            db_create_usuario(sb, payload)
            st.success("Cadastro criado. Aguarde liberação (status PENDENTE).")
        except Exception as e:
            st.error(f"Falha ao criar cadastro: {e}")


def pagina_recuperar(sb):
    st.header("Recuperar / Atualizar Cadastro")

    st.write(
        "Fluxo: você informa **email + telefone**. Geramos uma **senha temporária (10 min)**. "
        "Faça login com ela e o sistema vai obrigar você a **atualizar seus dados (exceto email)**."
    )

    email = st.text_input("Email cadastrado")
    telefone = st.text_input("Telefone cadastrado")

    if st.button("Gerar senha temporária", type="primary"):
        email_n = normalize_email(email)
        tel_n = only_digits(telefone)

        user = db_get_usuario_by_email(sb, email_n)
        if not user:
            st.error("Usuário não encontrado.")
            return

        if only_digits(user.get("telefone") or "") != tel_n:
            st.error("Telefone não confere.")
            return

        senha_tmp = gerar_senha_temporaria(8)
        exp = now_utc() + timedelta(minutes=10)

        try:
            db_update_usuario_by_email(
                sb,
                email_n,
                {
                    "senha_temporaria": senha_tmp,
                    "senha_temporaria_expira": exp.isoformat(),
                },
            )
            st.success("Senha temporária gerada.")
            st.info(f"Sua senha temporária (válida por 10 min): **{senha_tmp}**")
        except Exception as e:
            st.error(f"Falha ao gerar senha temporária: {e}")


def pagina_atualizar_cadastro_forcado(sb):
    st.header("Atualizar Cadastro (obrigatório)")

    user = st.session_state.get("usuario") or {}
    email = user.get("email")

    st.warning("Você entrou com senha temporária. Atualize seus dados. O email não pode ser alterado.")

    st.text_input("Email (bloqueado)", value=email, disabled=True)

    nome = st.text_input("Nome de Escala", value=user.get("nome") or "")
    telefone = st.text_input("Telefone", value=user.get("telefone") or "")

    col1, col2 = st.columns(2)
    with col1:
        graduacao = st.selectbox(
            "Graduação",
            GRADUACOES,
            index=GRADUACOES.index(user.get("graduacao")) if user.get("graduacao") in GRADUACOES else 0,
        )
        lotacao = st.text_input("Lotação", value=user.get("lotacao") or "")
    with col2:
        origem = st.selectbox(
            "Origem",
            ORIGENS,
            index=ORIGENS.index(user.get("origem")) if user.get("origem") in ORIGENS else 0,
        )

    nova_senha = st.text_input("Nova senha", type="password")
    nova_senha2 = st.text_input("Confirmar nova senha", type="password")

    if st.button("Salvar alterações", type="primary"):
        tel_n = only_digits(telefone)
        if not nome.strip():
            st.error("Informe o Nome de Escala.")
            return
        if len(tel_n) < 8:
            st.error("Informe um telefone válido.")
            return
        if not nova_senha or nova_senha != nova_senha2:
            st.error("Senha inválida ou não confere.")
            return

        try:
            db_update_usuario_by_email(
                sb,
                email,
                {
                    "nome": nome.strip(),
                    "telefone": tel_n,
                    "graduacao": graduacao,
                    "lotacao": lotacao.strip() or "-",
                    "origem": origem,
                    "senha": nova_senha,
                    # invalida senha temporária
                    "senha_temporaria": None,
                    "senha_temporaria_expira": None,
                },
            )
            st.success("Cadastro atualizado. Faça login novamente com sua nova senha.")
            logout()
        except Exception as e:
            st.error(f"Falha ao atualizar cadastro: {e}")


def pagina_registrar_presenca(sb):
    st.header("Registrar Presença")

    if not is_logged_in():
        st.info("Faça login para registrar presença.")
        return

    user = st.session_state.get("usuario")
    if not user:
        return

    if (user.get("status") or "").upper() != "ATIVO":
        st.warning("Seu cadastro ainda não está ATIVO.")
        return

    st.write("Confirme seus dados e clique em **Registrar**.")

    st.write(
        {
            "nome": user.get("nome"),
            "email": user.get("email"),
            "graduacao": user.get("graduacao"),
            "lotacao": user.get("lotacao"),
            "origem": user.get("origem"),
        }
    )

    if st.button("Registrar", type="primary"):
        payload = {
            "usuario_id": user.get("id"),
            "nome": user.get("nome"),
            "graduacao": user.get("graduacao"),
            "lotacao": user.get("lotacao"),
            "origem": user.get("origem"),
        }
        try:
            db_insert_presenca(sb, payload)
            st.success("Presença registrada.")
        except Exception as e:
            st.error(f"Falha ao registrar presença: {e}")


def pagina_admin(sb):
    st.header("Admin")

    # Admin simples: um PIN no secrets
    pin_cfg = str(st.secrets.get("ADMIN_PIN", "")).strip()
    if not pin_cfg:
        st.warning("ADMIN_PIN não configurado em secrets. Admin está desativado.")
        return

    pin = st.text_input("PIN do Admin", type="password")
    if pin != pin_cfg:
        st.info("Digite o PIN correto para acessar.")
        return

    tab1, tab2 = st.tabs(["Usuários", "Presenças"])

    with tab1:
        usuarios = db_list_usuarios(sb)
        st.write(f"Total: {len(usuarios)}")

        # Aprovar PENDENTE -> ATIVO
        pendentes = [u for u in usuarios if (u.get("status") or "").upper() != "ATIVO"]
        if pendentes:
            st.subheader("Liberar usuário")
            emails = [u.get("email") for u in pendentes if u.get("email")]
            alvo = st.selectbox("Selecione", emails)
            if st.button("Marcar como ATIVO"):
                try:
                    db_update_usuario_by_email(sb, alvo, {"status": "ATIVO"})
                    st.success("Atualizado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Falha: {e}")

        st.subheader("Lista")
        st.dataframe(
            [{k: u.get(k) for k in ["created_at", "nome", "email", "telefone", "graduacao", "lotacao", "origem", "status"]} for u in usuarios],
            use_container_width=True,
        )

    with tab2:
        presencas = db_list_presencas(sb)
        st.write(f"Total: {len(presencas)}")
        st.dataframe(
            [{k: p.get(k) for k in ["created_at", "nome", "graduacao", "lotacao", "origem", "usuario_id"]} for p in presencas],
            use_container_width=True,
        )


# ==========================================================
# App
# ==========================================================

def main():
    st.set_page_config(page_title="Rota Presença (Supabase)", layout="wide")
    st.title("Rota Presença (Supabase)")
    st.caption("Banco: Supabase Postgres • Streamlit")

    sb = require_supabase()

    if "usuario" not in st.session_state:
        st.session_state["usuario"] = None
    if "forcar_alteracao" not in st.session_state:
        st.session_state["forcar_alteracao"] = False

    with st.sidebar:
        st.subheader("Menu")
        op = st.radio(
            "Ir para:",
            ["Login", "Cadastro", "Recuperar", "Registrar Presença", "Admin"],
            index=0,
        )

        if is_logged_in():
            st.write(f"Logado: **{st.session_state['usuario'].get('email')}**")
            if st.button("Sair"):
                logout()
                st.rerun()

    # Se entrou com senha temporária, força essa tela antes de qualquer coisa
    if st.session_state.get("forcar_alteracao"):
        pagina_atualizar_cadastro_forcado(sb)
        return

    if op == "Login":
        pagina_login(sb)
    elif op == "Cadastro":
        pagina_cadastro(sb)
    elif op == "Recuperar":
        pagina_recuperar(sb)
    elif op == "Registrar Presença":
        pagina_registrar_presenca(sb)
    elif op == "Admin":
        pagina_admin(sb)


if __name__ == "__main__":
    main()
