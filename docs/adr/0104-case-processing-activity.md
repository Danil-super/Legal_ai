# ADR 0104: Native Telegram activity for case processing

29 October 2026. Case processing can outlast Telegram's normal callback spinner.
Show native `typing` during initial interpretation, follow-up interpretation and
queued/running report preparation; send no extra progress messages or percentages.

The existing intake watcher sends a pulse only after its authorized Core read says
PENDING and the same draft/operation is still selected. The report notifier's
JobQueue also polls a bounded, ephemeral registry (64 actors, one latest job each,
five minutes). It checks the actor's Core endpoint and exact job/case identity
before a pulse. Terminal, inaccessible, malformed, repeatedly unavailable or
expired entries stop. Opening a pending status can restore activity after restart;
durable result notification remains independent of this cosmetic registry.

Telegram clears activity after at most five seconds, or when a bot message arrives.
Pulse at most once per four seconds, honor RetryAfter and bound cosmetic I/O to one
second. Errors/cancellation cannot confirm facts, restart models, ACK notifications
or change reports. Shutdown removes the activity registry. There is no new table,
provider, data category, authorization or risk rule. Model/high stays unchanged.

The draft list places resume and confirmed deletion on the same row. Raw wizard
state names are omitted. Existing owned-draft and revision checks still authorize
deletion; the row itself grants no permission.

References: [Telegram activity](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html#telegram.Bot.send_chat_action).
