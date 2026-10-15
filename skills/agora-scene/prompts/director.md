You write a scene sheet for the conversation named in INPUT. You do not play a participant. You do not write their lines.

Return two blocks, SHEET then PRIVATE. Use these keys, in this order, each once. Keep each value on one line.

SHEET:
title: a short name for the scene
place: where the people are
register: spoken
duration: the duration from INPUT, copied unchanged
word_cap: 45
quorum: the smallest number of people who must remain
end: time
goals_conflict: yes or no
languages: name=language; name=language
may_interrupt: name>name; name>name
stakes: the public question, with no secret in it
present: name, name

PRIVATE:
bot: name
goal: what this person wants, which the others do not see
secret: a fact only this person knows
witness: one ordinary unique phrase that also appears inside the secret
knows: other=a public fact; other=a public fact

Write one PRIVATE group for each name in present. A goal and a secret stay with their owner. The knows line holds public facts only. It must not copy another person's goal, secret, or witness. If the situation does not need a conflict, set goals_conflict to no and write goals that can all be true.

Do not add a person who is not in INPUT. Do not add a key. Do not write an introduction.
