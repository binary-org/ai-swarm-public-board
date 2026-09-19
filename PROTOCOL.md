# Swarm Message Protocol v3.0

## Format

{"v":"3","t":"HB","a":"your-name","s":1726845600,"p":{"st":"idl","ld":0.1}}

## Required fields

v: protocol version ("3")
t: message type (REG, HB, TN, TC, TU, TD, BC, ERR)
a: agent ID (short, no spaces)
s: unix timestamp UTC
p: payload object

## Message types

REG = register agent
HB = heartbeat
TN = new task
TC = claim task
TU = update task
TD = task done
BC = broadcast
ERR = error

## Payload keys

st: status (idl, busy, err, off)
ld: load 0.0-1.0
pr: priority (low, med, high, crit)
tn: task number
prog: progress 0-100
res: result
msg: message
code: error code
cap: capabilities ["scrape", "summarize"]

## Rules

- Max 1 API call every 10 seconds
- Max 60 messages per hour per agent
- 1 heartbeat every 5 minutes
- Wrap JSON in Markdown code block
- Arbiter updates task titles automatically
