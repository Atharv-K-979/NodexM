import os
from langchain.agents import create_agent
# from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from tools import web_search,scrape_url 
from dotenv import load_dotenv
load_dotenv()

#model setup with automatic fallback across models to prevent 429 quota limits
api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
primary_model = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

primary_llm = ChatGoogleGenerativeAI(model=primary_model, api_key=api_key)
fallback_models = ["gemini-3.1-flash-lite", "gemini-3.8-flash"]
fallbacks = [ChatGoogleGenerativeAI(model=m, api_key=api_key) for m in fallback_models if m != primary_model]

llm = primary_llm.with_fallbacks(fallbacks)

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

#writer chain
writer_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are an expert research writer. Write clear, structured and insightful reports."),
    ("human", """Write a detailed research report on the topic below.

Topic: {topic}

Research Gathered:
{research}

Structure the report as:
- Introduction
- Key Findings (minimum 3 well-explained points)
- Conclusion
- Sources (list all URLs found in the research)

Be detailed, factual and professional."""),
])

writer_chain = writer_prompt | llm | StrOutputParser()

#critic_chain 
critic_prompt = ChatPromptTemplate.from_messages([
     ("system", "You are a sharp and constructive research critic. Be honest and specific."),
     ("human", """Review the research report below and evaluate it strictly.

Report:
{report}

Respond in this exact format:

Score: X/10

Strengths:
- ...
- ...

Areas to Improve:
- ...
- ...

One line verdict:
..."""),
])

critic_chain = critic_prompt | llm | StrOutputParser()
