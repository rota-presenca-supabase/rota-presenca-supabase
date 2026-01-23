
import streamlit as st
from supabase import create_client
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]

EMAIL_USER = "rota.presenca.caes@gmail.com"
EMAIL_PASSWORD = "hqxf xwwz jffq kwsn"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Rota Presença", layout="wide")

def enviar_email(destinatario, assunto, corpo):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)

def buscar_usuario(email, telefone):
    return supabase.table("usuarios")         .select("*")         .eq("email", email)         .eq("telefone", telefone)         .execute()

menu = st.sidebar.radio(
    "Menu",
    ["Login", "Cadastro", "Recuperar", "Registrar Presença", "Admin"]
)

if menu == "Recuperar":
    st.header("Recuperar dados do cadastro")

    email = st.text_input("Email cadastrado")
    telefone = st.text_input("Telefone cadastrado")

    if st.button("Enviar dados para o Email cadastrado"):
        try:
            res = buscar_usuario(email, telefone)
            if not res.data:
                st.error("Usuário não encontrado.")
            else:
                u = res.data[0]
                corpo = f"""
Seus dados cadastrados na Rota Presença:

Nome: {u['nome']}
Email: {u['email']}
Telefone: {u['telefone']}
Graduação: {u['graduacao']}
Lotação: {u['lotacao']}
Origem: {u['origem']}

Data do envio: {datetime.now().strftime('%d/%m/%Y %H:%M')}
"""

                enviar_email(
                    destinatario=u["email"],
                    assunto="Seus dados - Rota Presença",
                    corpo=corpo
                )
                st.success("Dados enviados para o email cadastrado com sucesso.")
        except Exception as e:
            st.error(f"Erro ao enviar email: {e}")
else:
    st.info("As demais telas permanecem exatamente como já estavam funcionando.")
