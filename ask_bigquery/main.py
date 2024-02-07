import google.api_core.exceptions
import google.cloud.bigquery as bq
import streamlit as st
from langchain_community.document_loaders import BigQueryLoader
from langchain_core.runnables import RunnablePassthrough
from langchain_core.prompts import PromptTemplate
from langchain_google_vertexai import VertexAI
from langchain_core.prompts.base import format_document


def sanitize_query(query):
    return '\n'.join(line for line in query.splitlines() if not line.startswith('```'))


def get_ddls_query(project, dataset):
    return f"""
            SELECT table_name, ddl
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES`
            WHERE table_type = 'BASE TABLE'
            ORDER BY table_name;"""


def load_ddls():
    print(f"load_ddls for {st.session_state.project}.{st.session_state.dataset}")
    ddls_docs = BigQueryLoader(
        get_ddls_query(st.session_state.project, st.session_state.dataset),
        metadata_columns="table_name", page_content_columns="ddl"
    ).load()
    ddls = "\n\n".join(
        format_document(doc, PromptTemplate.from_template("{page_content}"))
        for doc in ddls_docs
    )
    prompt = PromptTemplate.from_template(
        "Suggest a BigQuery sql query that answer this user request '{request}' :\n\n" + ddls
    )

    # Define the chain
    st.session_state.chain = ({"request": RunnablePassthrough()} | prompt | st.session_state.llm)
    st.toast(f"context updated from {st.session_state.project}.{st.session_state.dataset}")


def run():
    st.set_page_config(page_title="Ask BigQuery", page_icon="✨")
    st.title("✨ Ask BigQuery")

    with st.sidebar:
        with st.form("context"):
            st.write("Define your context")
            st.text_input("Project", key="project", value="bigquery-public-data")
            st.text_input("Dataset", key="dataset", value="iowa_liquor_sales")
            st.form_submit_button("Submit", on_click=load_ddls)

    st.session_state.llm = VertexAI(model_name="code-bison@002", max_output_tokens=2048)
    load_ddls()

    if "messages" not in st.session_state:
        st.session_state["messages"] = [{"role": "ai", "content": "How can I help you?"}]

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if "content" in msg:
                st.write(msg["content"])
            if "df" in msg:
                st.dataframe(msg["df"])
            if "error" in msg:
                st.error(msg["error"])

    if prompt := st.chat_input():
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.chat_message("user").write(prompt)

        with st.spinner("Thinking ..."):
            query = st.session_state.chain.invoke(prompt)
        message = "Sure, here's the corresponding query :\n\n" + query
        st.chat_message("ai").write(message)

        try:
            with st.spinner("Running the query ..."):
                bq_client = bq.Client()
                df = bq_client.query(sanitize_query(query)).result().to_dataframe()
            st.chat_message("ai").dataframe(df)
            st.session_state.messages.append({"role": "ai", "content": message, "df": df})
        except google.api_core.exceptions.ClientError as error:
            st.chat_message("ai").error(error)
            st.session_state.messages.append({"role": "ai", "content": message, "error": error})


if __name__ == '__main__':
    run()
