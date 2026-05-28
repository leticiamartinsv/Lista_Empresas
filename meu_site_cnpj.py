import json
import streamlit as st
import pandas as pd
import pandas_gbq
from google.oauth2.credentials import Credentials

# --- TRUQUE DE LOGIN PARA A NUVEM ---
creds = None
if "gcp_service_account" in st.secrets:
    # Lê o texto do crachá que está no Streamlit Secrets
    info = json.loads(st.secrets["gcp_service_account"])
    # Força a criação do crachá na memória (sem tentar abrir navegador)
    creds = Credentials.from_authorized_user_info(info)
# ------------------------------------

# ─── Configuração ────────────────────────────────────────────────────────────
MEU_PROJETO = "project-39977f6a-4a0c-4879-ae4"

st.set_page_config(page_title="Lista de empresas", layout="wide")
st.title("Lista de Empresas | Brasil")
st.caption("Dados da Receita Federal via Base dos Dados · Empresas ativas no Brasil · Atualizado até 11/2025")

# ─── Constantes ──────────────────────────────────────────────────────────────
TODOS_OS_ESTADOS = [
    "Todos", "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
    "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ",
    "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]

PORTES = {
    "Todos": None,
    "Microempresa (ME)": 1,
    "Empresa de Pequeno Porte (EPP)": 3,
    "Médio / Grande Porte": 5,
    "Não Informado": 0,
}

TIPO_UNIDADE = {
    "Todos": None,
    "Apenas Matrizes": 1,
    "Apenas Filiais": 2,
}
# ─── Função de Busca com Cache ───────────────────────────────────────────────
@st.cache_data(ttl=86400) # Guarda o resultado por 24 horas para não pagar de novo
def buscar_dados_no_google(query_sql):
    return pandas_gbq.read_gbq(query_sql, project_id=MEU_PROJETO, credentials=creds)
# ─── Barra lateral com filtros ───────────────────────────────────────────────
with st.sidebar:
    st.header("Filtros de Busca")
    
    # Criamos o formulário aqui:
    with st.form("form_filtros"):
        # Localização
        st.subheader("Localização")
        estado = st.selectbox("Estado (UF)", TODOS_OS_ESTADOS, index=TODOS_OS_ESTADOS.index("SP"))
        municipio_input = st.text_input("Município (opcional)", placeholder="ex: RECIFE")

        # Atividade
        st.subheader("Atividade")
        cnae_input = st.text_input(
            "CNAE Principal (obrigatório)",
            placeholder="ex: 5611201 — Restaurantes e similares",
        )
        cnae_secundario = st.text_input(
            "CNAE Secundário (opcional)",
            placeholder="ex: 4711301",
        )

        # Empresa
        st.subheader("Perfil da Empresa")
        porte = st.selectbox("Porte", list(PORTES.keys()))
        tipo_unidade = st.selectbox("Tipo de Unidade", list(TIPO_UNIDADE.keys()))

        # Contato
        st.subheader(" Contato")
        apenas_com_email = st.checkbox("Apenas com e-mail cadastrado")
        apenas_com_telefone = st.checkbox("Apenas com telefone cadastrado")

       # Volume
        st.subheader("Volume")
        limite = st.slider("Máximo de registros", 500, 500000, 5000, step=500)

        buscar = st.form_submit_button("Buscar Empresas", use_container_width=True, type="primary")

# ─── Lógica de busca ─────────────────────────────────────────────────────────
if buscar:
    cnae_clean = cnae_input.strip().replace(".", "").replace("-", "").replace("/", "")

    if not cnae_clean:
        st.warning("Digite pelo menos um CNAE Principal para buscar.")
        st.stop()

    # Monta filtros WHERE dinamicamente
    filtros = [
        "CAST(e.situacao_cadastral AS STRING) IN ('2', '02')",     # Garante apenas ATIVAS,                              
        f"CAST(e.cnae_fiscal_principal AS STRING) LIKE '%{cnae_clean}%'",
    ]

    if estado != "Todos":
        filtros.append(f"e.sigla_uf = '{estado}'")

    if municipio_input.strip():
        filtros.append(
            f"UPPER(mun.nome) LIKE '%{municipio_input.strip().upper()}%'"
        )

    porte_code = PORTES[porte]
    if porte_code is not None:
        filtros.append(f"emp.porte = '{porte_code}'")

    tipo_code = TIPO_UNIDADE[tipo_unidade]
    if tipo_code is not None:
        filtros.append(f"e.identificador_matriz_filial = '{tipo_code}'")

    if cnae_secundario.strip():
        cnae_sec_clean = cnae_secundario.strip().replace(".", "").replace("-", "").replace("/", "")
        filtros.append(f"e.cnae_fiscal_secundaria LIKE '%{cnae_sec_clean}%'")

    if apenas_com_email:
        filtros.append("e.email IS NOT NULL AND e.email != ''")

    if apenas_com_telefone:
        filtros.append("e.telefone_1 IS NOT NULL AND e.telefone_1 != ''")

    where_clause = "\n          AND ".join(filtros)

    # ── Query principal ──────────────────────────────────────────────────────
    # Tabelas corretas da RFB no Base dos Dados:
    #   br_rfb_cnpj.estabelecimentos  → endereço, CNAE, situação, contato
    #   br_rfb_cnpj.empresas          → razão social, porte, capital
    #   br_bd_diretorios_brasil.municipio → nome do município
    query = f"""
    -- ► Subquery emp: garante 1 linha por cnpj_basico
    WITH emp AS (
        SELECT
            cnpj_basico,
            ANY_VALUE(razao_social)   AS razao_social,
            ANY_VALUE(porte)          AS porte,
            ANY_VALUE(capital_social) AS capital_social
        FROM `basedosdados.br_me_cnpj.empresas`
        GROUP BY cnpj_basico
    ),
    -- ► Subquery mun: Usa o id_municipio padrão da Base dos Dados
    mun AS (
        SELECT
            CAST(id_municipio AS STRING) AS id_mun,
            ANY_VALUE(nome)              AS nome
        FROM `basedosdados.br_bd_diretorios_brasil.municipio`
        GROUP BY id_municipio
    )

    SELECT
        CONCAT(e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv)  AS cnpj,
        emp.razao_social,
        e.nome_fantasia,
        e.cnae_fiscal_principal                         AS cnae_principal,
        CASE emp.porte
            WHEN '0' THEN 'Não informado'
            WHEN '1' THEN 'Microempresa'
            WHEN '3' THEN 'Pequeno Porte'
            WHEN '5' THEN 'Demais'
            ELSE COALESCE(CAST(emp.porte AS STRING), '?')
        END                                             AS porte,
        CASE e.identificador_matriz_filial
            WHEN '1' THEN 'Matriz'
            WHEN '2' THEN 'Filial'
            ELSE '?'
        END                                             AS tipo_unidade,
        CONCAT(
            COALESCE(e.tipo_logradouro, ''), ' ',
            COALESCE(e.logradouro, ''), ', ',
            COALESCE(e.numero, 'S/N')
        )                                               AS endereco,
        e.bairro,
        mun.nome                                        AS municipio,
        e.sigla_uf                                      AS uf,
        e.cep,
        CONCAT(
            COALESCE(e.ddd_1, ''), ' ',
            COALESCE(e.telefone_1, '')
        )                                               AS telefone,
        e.email                                         AS email,
        emp.capital_social                              AS capital_social,
        e.data_inicio_atividade
    FROM `basedosdados.br_me_cnpj.estabelecimentos` e
    LEFT JOIN emp
           ON e.cnpj_basico = emp.cnpj_basico
    LEFT JOIN mun
           ON CAST(e.id_municipio AS STRING) = mun.id_mun
    WHERE {where_clause}
    -- Elimina o histórico duplicado, priorizando a linha com nome fantasia e telefone preenchidos
    QUALIFY ROW_NUMBER() OVER(
        PARTITION BY e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv 
        ORDER BY e.nome_fantasia DESC, e.telefone_1 DESC
    ) = 1
    
    ORDER BY emp.razao_social
    LIMIT {limite}
    """

    # Exibe a query no expander para facilitar debug
    with st.expander("🛠 Ver SQL gerado"):
        st.code(query, language="sql")

    with st.spinner("Consultando a Receita Federal via BigQuery… Aguarde."):
        try:
            # Chama a função com cache em vez de ir direto no Google
            df = buscar_dados_no_google(query)

            if df.empty:
                st.error(
                    "Nenhuma empresa encontrada com esses filtros. "
                    "Tente ampliar os critérios (ex.: remover município ou mudar o porte)."
                )
            else:
                # ── Métricas resumo ──────────────────────────────────────────
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Empresas encontradas", f"{len(df):,}")
                c2.metric(
                    "Com e-mail",
                    f"{df['email'].notna().sum():,}"
                    if "email" in df.columns else "–",
                )
                c3.metric(
                    "Com telefone",
                    f"{(df['telefone'].str.strip() != '').sum():,}"
                    if "telefone" in df.columns else "–",
                )
                c4.metric(
                    "UFs presentes",
                    df["uf"].nunique() if "uf" in df.columns else "–",
                )

                st.success(f"✅ {len(df):,} empresas carregadas!")

                # ── Tabela interativa (Mostra só as 100 primeiras para não travar) ──
                st.caption("Visualizando as 100 primeiras empresas (Baixe o CSV para ver todas)")
                st.dataframe(df.head(100), use_container_width=True, height=420)

                # ── Downloads ────────────────────────────────────────────────
                dl1, dl2 = st.columns(2)

                csv = df.to_csv(index=False, sep=";").encode("utf-8-sig")
                dl1.download_button(
                    label="Baixar CSV (Excel)",
                    data=csv,
                    file_name=f"leads_{estado}_{cnae_clean}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

                # Versão só com e-mails preenchidos
                if "email" in df.columns:
                    df_email = df[df["email"].notna() & (df["email"] != "")]
                    if not df_email.empty:
                        csv_email = df_email.to_csv(index=False, sep=";").encode("utf-8-sig")
                        dl2.download_button(
                            label=f"Só com e-mail ({len(df_email):,})",
                            data=csv_email,
                            file_name=f"leads_email_{estado}_{cnae_clean}.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )

        except Exception as e:
            st.error(f"❌ Erro na consulta BigQuery:\n\n`{e}`")
            st.info(
                "Possíveis causas:\n"
    
                "CNAE inválido ou sem resultado para essa combinação de filtros"
            )
