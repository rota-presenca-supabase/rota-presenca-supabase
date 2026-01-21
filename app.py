import streamlit as st
from supabase import create_client
import pandas as pd
from datetime import datetime, timedelta, time
import pytz
import random
import re
from fpdf import FPDF
import urllib.parse

# ==========================================================
# CONFIG
# ==========================================================
st.set_page_config(page_title="Rota Nova Iguaçu", layout="centered")

FUSO_BR = pytz.timezone("America/Sao_Paulo")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================================
# UTIL
# ==========================================================
def br_now():
    return datetime.now(FUSO_BR)

def tel_only_digits(s):
    return re.sub(r"\D+", "", str(s or ""))

def tel_is_valid_11(s):
    return len(tel_only_digits(s)) == 11

def gerar_senha_temp(tam=10):
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(chars) for _ in range(tam))

# ==========================================================
# CACHE
# ==========================================================
@st.cache_data(ttl=10)
def get_usuarios():
    return supabase.table("usuarios").select("*").execute().data

@st.cache_data(ttl=5)
def get_presencas():
    return supabase.table("presencas").select("*").execute().data

# ==========================================================
# LOGIN / CADASTRO / RECUPERAR (IGUAL AO SHEETS)
# ==========================================================
if "usuario" not in st.session_state:
    st.session_state.usuario = None
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "force_update" not in st.session_state:
    st.session_state.force_update = False

tabs = st.tabs(["Login", "Cadastro", "Recuperar", "Admin"])

# ---------------- LOGIN ----------------
with tabs[0]:
    email = st.text_input("E-mail")
    tel = st.text_input("Telefone")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        usuarios = get_usuarios()
        tel_d = tel_only_digits(tel)

        for u in usuarios:
            if (
                u["email"].lower() == email.lower()
                and tel_only_digits(u["telefone"]) == tel_d
                and (u["senha"] == senha or (
                    u["temp_senha"] == senha
                    and u["temp_usada"] == "NAO"
                    and br_now() <= datetime.fromisoformat(u["temp_expira"])
                ))
            ):
                if u["status"] != "ATIVO":
                    st.error("Usuário não aprovado.")
                    st.stop()

                st.session_state.usuario = u
                if u["temp_senha"] == senha:
                    st.session_state.force_update = True
                st.rerun()

        st.error("Dados incorretos")

# ---------------- CADASTRO ----------------
with tabs[1]:
    nome = st.text_input("Nome de Escala")
    email = st.text_input("E-mail")
    telefone = st.text_input("Telefone")

    graduacao = st.selectbox(
        "Graduação",
        ["TCEL","MAJ","CAP","1º TEN","2º TEN","SUBTEN",
         "1º SGT","2º SGT","3º SGT","CB","SD","FC COM","FC TER"]
    )

    lotacao = st.text_input("Lotação")
    origem = st.selectbox("Origem", ["QG","RMCF","OUTROS"])
    senha = st.text_input("Senha", type="password")

    if st.button("Criar cadastro"):
        if not all([nome,email,telefone,graduacao,lotacao,origem,senha]):
            st.error("Preencha tudo")
            st.stop()

        supabase.table("usuarios").insert({
            "nome": nome,
            "email": email.lower(),
            "telefone": telefone,
            "graduacao": graduacao,
            "lotacao": lotacao,
            "origem": origem,
            "senha": senha,
            "status": "PENDENTE"
        }).execute()

        st.success("Cadastro criado. Aguarde aprovação.")

# ---------------- RECUPERAR ----------------
with tabs[2]:
    e = st.text_input("E-mail cadastrado")
    t = st.text_input("Telefone cadastrado")

    if st.button("Gerar senha temporária"):
        for u in get_usuarios():
            if u["email"].lower() == e.lower() and tel_only_digits(u["telefone"]) == tel_only_digits(t):
                temp = gerar_senha_temp()
                exp = br_now() + timedelta(minutes=10)

                supabase.table("usuarios").update({
                    "temp_senha": temp,
                    "temp_expira": exp.isoformat(),
                    "temp_usada": "NAO"
                }).eq("id", u["id"]).execute()

                st.success(f"Senha temporária: {temp}")
                st.stop()

        st.error("Dados não encontrados")

# ==========================================================
# USUÁRIO LOGADO
# ==========================================================
if st.session_state.usuario:
    u = st.session_state.usuario
    st.sidebar.success(f"{u['graduacao']} {u['nome']}")
    if st.sidebar.button("Sair"):
        st.session_state.clear()
        st.rerun()

    # FORÇAR UPDATE APÓS SENHA TEMP
    if st.session_state.force_update:
        st.warning("Atualize seu cadastro (exceto e-mail)")

        novo_nome = st.text_input("Nome", u["nome"])
        nova_senha = st.text_input("Nova senha", type="password")

        if st.button("Salvar atualização"):
            supabase.table("usuarios").update({
                "nome": novo_nome,
                "senha": nova_senha,
                "temp_usada": "SIM",
                "temp_senha": None,
                "temp_expira": None
            }).eq("id", u["id"]).execute()

            st.success("Cadastro atualizado")
            st.session_state.force_update = False
            st.rerun()

    # PRESENÇA
    st.header("Registrar presença")

    presencas = get_presencas()
    emails = [p["email"] for p in presencas]

    if u["email"] in emails:
        st.success("Presença já registrada")
    else:
        if st.button("Confirmar presença"):
            supabase.table("presencas").insert({
                "usuario_id": u["id"],
                "nome": u["nome"],
                "graduacao": u["graduacao"],
                "lotacao": u["lotacao"],
                "origem": u["origem"],
                "email": u["email"]
            }).execute()
            st.rerun()

    if presencas:
        df = pd.DataFrame(presencas)
        st.dataframe(df[["graduacao","nome","lotacao","origem"]])
