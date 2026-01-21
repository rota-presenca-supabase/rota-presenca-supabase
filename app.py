import streamlit as st
from supabase import create_client
import bcrypt
from datetime import datetime

# =============================
# CONFIG
# =============================
st.set_page_config(page_title="Rota Presença (Supabase)", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]

supabase_public = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# =============================
# CONSTANTES
# =============================
GRADUACOES = [
    "TCEL", "MAJ", "CAP", "1º TEN", "2º TEN",
    "SUBTEN", "1º SGT", "2º SGT", "3º SGT",
    "CB", "SD", "FC COM", "FC TER"
]

ORIGENS = ["QG", "RMCF", "OUTROS"]

# =============================
# FUNÇÕES
# =============================
def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

def verificar_senha(senha: str, hash_salvo: str) -> bool:
    return bcrypt.checkpw(senha.encode(), hash_salvo.encode())

# =============================
# MENU
# =============================
st.sidebar.title("Menu")
pagina = st.sidebar.radio(
    "Ir para:",
    ["Login", "Cadastro", "Recuperar", "Registrar Presença", "Admin"]
)

st.title("Rota Presença (Supabase)")
st.caption("Banco: Supabase Postgres • Streamlit")

# =============================
# CADASTRO
# =============================
if pagina == "Cadastro":
    st.header("Cadastro")

    col1, col2 = st.columns(2)

    with col1:
        nome = st.text_input("Nome de Escala")
        email = st.text_input("Email")
        telefone = st.text_input("Telefone")
        senha = st.text_input("Senha", type="password")

    with col2:
        graduacao = st.selectbox("Graduação", GRADUACOES)
        lotacao = st.text_input("Lotação")
        origem = st.selectbox("Origem", ORIGENS)
        confirmar = st.text_input("Confirmar senha", type="password")

    if st.button("Criar cadastro"):
        if not all([nome, email, telefone, senha, confirmar]):
            st.error("Preencha todos os campos.")
        elif senha != confirmar:
            st.error("As senhas não coincidem.")
        else:
            try:
                senha_hash = hash_senha(senha)

                supabase_admin.table("usuarios").insert({
                    "nome": nome,
                    "email": email,
                    "telefone": telefone,
                    "graduacao": graduacao,
                    "lotacao": lotacao,
                    "origem": origem,
                    "senha": senha_hash,
                    "status": "ATIVO",
                    "created_at": datetime.utcnow().isoformat()
                }).execute()

                st.success("Cadastro realizado com sucesso!")

            except Exception as e:
                st.error(f"Erro ao cadastrar: {e}")

# =============================
# LOGIN
# =============================
elif pagina == "Login":
    st.header("Login")

    email = st.text_input("Email")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        try:
            resp = supabase_public.table("usuarios").select("*").eq("email", email).execute()

            if not resp.data:
                st.error("Usuário não encontrado.")
            else:
                user = resp.data[0]
                if verificar_senha(senha, user["senha"]):
                    st.session_state["usuario"] = user
                    st.success("Login realizado!")
                else:
                    st.error("Senha inválida.")
        except Exception as e:
            st.error(f"Erro no login: {e}")

# =============================
# REGISTRAR PRESENÇA
# =============================
elif pagina == "Registrar Presença":
    st.header("Registrar Presença")

    if "usuario" not in st.session_state:
        st.warning("Faça login primeiro.")
    else:
        user = st.session_state["usuario"]

        if st.button("Registrar presença agora"):
            try:
                supabase_public.table("presencas").insert({
                    "usuario_id": user["id"],
                    "nome": user["nome"],
                    "graduacao": user["graduacao"],
                    "lotacao": user["lotacao"],
                    "origem": user["origem"],
                    "created_at": datetime.utcnow().isoformat()
                }).execute()

                st.success("Presença registrada com sucesso!")
            except Exception as e:
                st.error(f"Erro ao registrar presença: {e}")

# =============================
# ADMIN
# =============================
elif pagina == "Admin":
    st.header("Admin")

    try:
        dados = supabase_admin.table("presencas").select("*").order("created_at", desc=True).execute()
        st.dataframe(dados.data)
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")

# =============================
# RECUPERAR (placeholder)
# =============================
elif pagina == "Recuperar":
    st.info("Recuperação será implementada na próxima etapa.")
