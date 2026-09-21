from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from tools import web_search,scrape_url 
from dotenv import load_dotenv
load_dotenv()

#model setup
llm = ChatOpenAI(model = "gpt-4o-mini",temperature=0)

#1st agent
def build_search_agent():
    return create_agent(
        model = llm,
        tools= [web_search]
    )

#2nd agent
def build_reader_agent():
    return create_agent(
        model = llm,
        tools = [scrape_url]
    )
