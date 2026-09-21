from agents import build_reader_agent,build_search_agent, writer_chain , critic_chain

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else getattr(item, "text", str(item))
            for item in content
        )
    return str(content)

def run_research_pipeline(topic : str) -> dict:

    state = {}

    #search agent working 
    print("\n"+" ="*50)
    print("step 1 - search agent is working ...")
    print("="*50)

    search_agent = build_search_agent()
    search_result = search_agent.invoke({
        "messages" : [("user", f"Find recent, reliable and detailed information about: {topic}. Include the source URLs.")]
    })
    state["search_results"] = _extract_text(search_result['messages'][-1].content)

    print("\n search result ",state['search_results'])

    # Capture raw tool outputs so reader agent is guaranteed to have actual URLs
    tool_outputs = [str(m.content) for m in search_result.get('messages', []) if getattr(m, 'type', '') == 'tool']
    search_context = "\n".join(tool_outputs) if tool_outputs else state["search_results"]

    #step 2 - reader agent 
    print("\n"+" ="*50)
    print("step 2 - Reader agent is scraping top resources ...")
    print("="*50)

    reader_agent = build_reader_agent()
    reader_result = reader_agent.invoke({
        "messages": [("user",
            f"Based on these search sources for '{topic}', "
            f"pick the single best URL, call scrape_url once with that URL, and summarize the key facts.\n\n"
            f"Sources:\n{search_context[:2500]}"
        )]
    })

    state['scraped_content'] = _extract_text(reader_result['messages'][-1].content)

    print("\nscraped content: \n", state['scraped_content'])

    #step 3 - writer chain 

    print("\n"+" ="*50)
    print("step 3 - Writer is drafting the report ...")
    print("="*50)

    research_combined = (
        f"SEARCH RESULTS : \n {state['search_results']} \n\n"
        f"DETAILED SCRAPED CONTENT : \n {state['scraped_content']}"
    )

    state["report"] = writer_chain.invoke({
        "topic" : topic,
        "research" : research_combined
    })

    print("\n Final Report\n",state['report'])

    #critic report 

    print("\n"+" ="*50)
    print("step 4 - critic is reviewing the report ")
    print("="*50)

    state["feedback"] = critic_chain.invoke({
        "report":state['report']
    })

    print("\n critic report \n", state['feedback'])

    return state



if __name__ == "__main__":
    topic = input("\n Enter a research topic : ")
    run_research_pipeline(topic)
