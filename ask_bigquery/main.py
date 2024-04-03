from typing import List

import google.api_core.exceptions
import google.cloud.bigquery as bq
import streamlit as st
from langchain_community.document_loaders import BigQueryLoader
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langchain_core.prompts.base import format_document
from langchain_core.runnables import RunnablePassthrough
from langchain_google_vertexai import VertexAI


def sanitize_query(query):
    return '\n'.join(line for line in query.splitlines() if not line.startswith('```'))


def get_ddls_query(project, dataset):
    return f"""
            SELECT table_name, ddl
            FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES`
            WHERE table_type = 'BASE TABLE'
            ORDER BY table_name;"""


def get_bigquery_models(project, dataset) -> List[Document]:
    bq_client = bq.Client(project=project)
    models = bq_client.list_models(dataset)
    return [Document(page_content=f"ddl: CREATE OR REPLACE MODEL {m.project}.{m.dataset_id}.{m.model_id} "
                                  f"OPTIONS (model_type='{m.model_type}');",
                     metadata={"model_id": m.model_id})
            for m in models]


def load_context():
    print(f"load_context for {st.session_state.project}.{st.session_state.dataset}")
    ddls_docs = BigQueryLoader(
        get_ddls_query(st.session_state.project, st.session_state.dataset),
        metadata_columns="table_name", page_content_columns="ddl"
    ).load()
    models_docs = get_bigquery_models(st.session_state.project, st.session_state.dataset)
    ddls = "\n\n".join(
        format_document(doc, PromptTemplate.from_template("{page_content}"))
        for doc in ddls_docs + models_docs
    )
    prompt = PromptTemplate.from_template(
        "Suggest a BigQuery sql query that answer this user request '{request}' :\n\n" + ddls + "\n\n"
    )
    print(prompt.template)

    # Define the chain
    st.session_state.llm = VertexAI(model_name=st.session_state.model, max_output_tokens=2048)
    st.session_state.chain = ({"request": RunnablePassthrough()} | prompt | st.session_state.llm)
    st.toast(f"context updated from {st.session_state.project}.{st.session_state.dataset} using {st.session_state.model}")


def run():
    st.set_page_config(page_title="Ask BigQuery", page_icon="✨")
    st.title("✨ Ask BigQuery")

    with st.sidebar:
        with st.form("context"):
            st.write("Define your context")
            st.selectbox("Model", ("gemini-pro", "code-bison"), key="model")
            st.text_input("Project", key="project", value="bigquery-public-data")
            st.text_input("Dataset", key="dataset", value="iowa_liquor_sales")
            st.form_submit_button("Submit", on_click=load_context)

    load_context()

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
