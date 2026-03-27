import pytest
from codyclaw.gateway.router import MessageRouter, AgentConfig
from codyclaw.channel.base import IncomingMessage


def make_msg(
    chat_type: str = "p2p",
    sender_id: str = "user-1",
    chat_id: str = "chat-1",
    content: str = "hello",
    is_mention_bot: bool = False,
) -> IncomingMessage:
    return IncomingMessage(
        message_id="msg-1",
        chat_id=chat_id,
        chat_type=chat_type,
        sender_id=sender_id,
        sender_name=sender_id,
        content=content,
        msg_type="text",
        is_mention_bot=is_mention_bot,
    )


def make_agent(
    agent_id: str = "agent-1",
    trigger_mode: str = "all",
    allowed_users: list = None,
    allowed_groups: list = None,
    prefix: str = "/",
) -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        name="Test Agent",
        workdir="/tmp",
        trigger_mode=trigger_mode,
        allowed_users=allowed_users or [],
        allowed_groups=allowed_groups or [],
        prefix=prefix,
    )


# --- Default agent routing ---

def test_default_agent_resolves_p2p():
    router = MessageRouter()
    agent = make_agent()
    router.register_agent(agent)
    router.set_default_agent("agent-1")
    assert router.resolve(make_msg(chat_type="p2p")) == agent


def test_no_default_agent_returns_none():
    router = MessageRouter()
    assert router.resolve(make_msg()) is None


# --- allowed_users ---

def test_allowed_users_blocks_unauthorized():
    router = MessageRouter()
    router.register_agent(make_agent(allowed_users=["user-allowed"]))
    router.set_default_agent("agent-1")
    assert router.resolve(make_msg(sender_id="user-blocked")) is None


def test_allowed_users_permits_authorized():
    router = MessageRouter()
    router.register_agent(make_agent(allowed_users=["user-1"]))
    router.set_default_agent("agent-1")
    assert router.resolve(make_msg(sender_id="user-1")) is not None


def test_empty_allowed_users_permits_all():
    router = MessageRouter()
    router.register_agent(make_agent(allowed_users=[]))
    router.set_default_agent("agent-1")
    assert router.resolve(make_msg(sender_id="anyone")) is not None


# --- allowed_groups (S6 fix) ---

def test_allowed_groups_blocks_unlisted_group():
    router = MessageRouter()
    router.register_agent(make_agent(allowed_groups=["group-allowed"]))
    router.set_default_agent("agent-1")
    msg = make_msg(chat_type="group", chat_id="group-blocked")
    assert router.resolve(msg) is None


def test_allowed_groups_permits_listed_group():
    router = MessageRouter()
    router.register_agent(make_agent(allowed_groups=["group-1"]))
    router.set_default_agent("agent-1")
    msg = make_msg(chat_type="group", chat_id="group-1")
    assert router.resolve(msg) is not None


def test_empty_allowed_groups_permits_all_groups():
    router = MessageRouter()
    router.register_agent(make_agent(allowed_groups=[]))
    router.set_default_agent("agent-1")
    msg = make_msg(chat_type="group", chat_id="any-group")
    assert router.resolve(msg) is not None


def test_allowed_groups_does_not_affect_p2p():
    """Group whitelist should not block p2p messages."""
    router = MessageRouter()
    router.register_agent(make_agent(allowed_groups=["group-1"]))
    router.set_default_agent("agent-1")
    msg = make_msg(chat_type="p2p")
    assert router.resolve(msg) is not None


# --- Group binding + trigger modes ---

def test_group_mention_mode_requires_at():
    router = MessageRouter()
    router.register_agent(make_agent(trigger_mode="mention"))
    router.bind_group("group-1", "agent-1")

    msg_no_at = make_msg(chat_type="group", chat_id="group-1", is_mention_bot=False)
    assert router.resolve(msg_no_at) is None

    msg_at = make_msg(chat_type="group", chat_id="group-1", is_mention_bot=True)
    assert router.resolve(msg_at) is not None


def test_group_prefix_mode():
    router = MessageRouter()
    router.register_agent(make_agent(trigger_mode="prefix", prefix="/"))
    router.bind_group("group-1", "agent-1")

    msg_no_prefix = make_msg(chat_type="group", chat_id="group-1", content="hello")
    assert router.resolve(msg_no_prefix) is None

    msg_prefix = make_msg(chat_type="group", chat_id="group-1", content="/hello")
    assert router.resolve(msg_prefix) is not None


# --- User binding ---

def test_user_binding_overrides_default():
    router = MessageRouter()
    agent1 = make_agent(agent_id="agent-1")
    agent2 = make_agent(agent_id="agent-2")
    router.register_agent(agent1)
    router.register_agent(agent2)
    router.set_default_agent("agent-1")
    router.bind_user("user-special", "agent-2")

    assert router.resolve(make_msg(sender_id="user-special")) == agent2
    assert router.resolve(make_msg(sender_id="user-normal")) == agent1


# --- Public API (M6 fix) ---

def test_get_agent_returns_registered():
    router = MessageRouter()
    agent = make_agent()
    router.register_agent(agent)
    assert router.get_agent("agent-1") == agent


def test_get_agent_returns_none_for_missing():
    router = MessageRouter()
    assert router.get_agent("nonexistent") is None


def test_iter_agents_returns_all():
    router = MessageRouter()
    a1 = make_agent(agent_id="a1")
    a2 = make_agent(agent_id="a2")
    router.register_agent(a1)
    router.register_agent(a2)
    ids = {c.agent_id for c in router.iter_agents()}
    assert ids == {"a1", "a2"}


# --- Group binding + allowed_users (M1 fix) ---

def test_group_binding_respects_allowed_users():
    """Group binding should enforce allowed_users whitelist."""
    router = MessageRouter()
    router.register_agent(make_agent(trigger_mode="all", allowed_users=["user-allowed"]))
    router.bind_group("group-1", "agent-1")

    msg_blocked = make_msg(chat_type="group", chat_id="group-1", sender_id="user-blocked")
    assert router.resolve(msg_blocked) is None

    msg_allowed = make_msg(chat_type="group", chat_id="group-1", sender_id="user-allowed")
    assert router.resolve(msg_allowed) is not None


def test_group_binding_empty_allowed_users_permits_all():
    """Empty allowed_users means everyone is allowed in group binding path."""
    router = MessageRouter()
    router.register_agent(make_agent(trigger_mode="all", allowed_users=[]))
    router.bind_group("group-1", "agent-1")

    msg = make_msg(chat_type="group", chat_id="group-1", sender_id="anyone")
    assert router.resolve(msg) is not None
