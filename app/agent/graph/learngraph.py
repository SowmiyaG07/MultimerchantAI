from langgraph.graph import StateGraph, MessagesState,START,END



def Merchantsupervisoragent(state: MessagesState):
    
      return {
        "messages": [
            {"role": "assistant", "content": "helloworld"}
        ]
    }

graphoutput = StateGraph(MessagesState)
graphoutput.add_node("Merchantsupervisor",Merchantsupervisoragent)

graphoutput.add_edge(START,"Merchantsupervisor")

graphoutput.add_edge("Merchantsupervisor",END)

graphout = graphoutput.compile()

result = graphout.invoke({
    "messages": [
        {"role": "user", "content": "Helloworld"}
    ]
})

print(result)