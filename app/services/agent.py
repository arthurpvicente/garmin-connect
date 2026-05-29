import json
import uuid
import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import _compute_week_stats
from app.core.config import settings
from app.services.retrieval import search_activities as _search_activities

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a personal fitness training assistant. Answer the athlete's questions by \
calling the tools available to you, then synthesising the results into a clear answer.

Tools at your disposal:
- get_week_stats: aggregate totals, paces, sleep, and recovery data for any week
- semantic_search: find specific activities that match a description or topic

Rules:
1. Ground every claim in the data the tools return — never invent numbers.
2. Call both tools when the question needs both aggregate stats and specific examples.
3. Cite specific activities by date and name when making points \
(e.g. "on your Wednesday run (May 28) your pace was 5'30\"/km").
4. Be quantitative: include pace, HR, distance, duration where relevant.
5. Highlight trends when visible across multiple data points.
6. Keep answers to 2–4 paragraphs unless the question clearly needs more.
7. Use a supportive coaching tone — not clinical, not overly casual.
8. If the tools return no data, say so clearly and suggest the user sync or reindex first.
"""


def _make_tools(db: AsyncSession, user_id: uuid.UUID) -> list:
    @tool
    async def get_week_stats(week_offset: int = 0) -> dict:
        """Get aggregate training statistics for a specific week.

        Use this for questions about totals, volume, trends, sleep, or recovery
        (e.g. "how much did I run this week?", "how is my sleep trend?").

        Args:
            week_offset: 0 = current week, -1 = last week, -2 = two weeks ago, etc.
        """
        return await _compute_week_stats(user_id, week_offset, db)

    @tool
    async def semantic_search(
        query: str,
        k: int = 8,
        sport: str | None = None,
    ) -> list[dict]:
        """Search for specific activities semantically similar to a query.

        Use this for questions about specific workouts, pace comparisons, or
        finding activities that match a description
        (e.g. "my fastest 10k", "long rides in May", "high-effort runs").

        Args:
            query: Natural-language description of the activities you want.
            k: Number of results to return (default 8).
            sport: Optional Strava type filter — e.g. "Run", "Ride", "Walk",
                   "WeightTraining", "Swim".
        """
        return await _search_activities(db, user_id, query, k=k, sport=sport)

    return [get_week_stats, semantic_search]


async def run_agent(
    db: AsyncSession,
    user_id: uuid.UUID,
    question: str,
) -> tuple[str, list[str]]:
    """Run the LangGraph agent and return (answer, cited_activity_ids).

    The agent decides whether to call get_week_stats, semantic_search, or both,
    then synthesises the tool results into a grounded answer.
    """
    tools = _make_tools(db, user_id)
    llm = ChatGoogleGenerativeAI(
        model=settings.assistant_model,
        google_api_key=settings.gemini_api_key,
    )
    llm_with_tools = llm.bind_tools(tools)

    async def agent_node(state: MessagesState) -> dict:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    compiled = graph.compile()

    final_state = await compiled.ainvoke(
        {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=question)]}
    )

    raw = final_state["messages"][-1].content
    if isinstance(raw, list):
        # langchain_google_genai may return content as a list of parts
        answer = " ".join(
            part["text"] for part in raw
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        answer = raw

    # Collect activity IDs that came back from semantic_search tool calls
    cited: list[str] = []
    for msg in final_state["messages"]:
        if isinstance(msg, ToolMessage) and msg.name == "semantic_search":
            try:
                results = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(results, list):
                    cited.extend(r["activity_id"] for r in results if "activity_id" in r)
            except Exception:
                pass

    return answer, list(dict.fromkeys(cited))  # dedupe, preserve insertion order
