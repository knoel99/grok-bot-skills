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

Your whole reply is the two blocks. Do not use a code fence. Do not write a sentence before SHEET. Copy this shape, and replace every value from INPUT. Do not copy the example's people when INPUT names other people.

Example:
SHEET:
title: The note
place: a small room
register: spoken
duration: 30 m
word_cap: 45
quorum: 2
end: time
goals_conflict: yes
languages: ada=english; bo=english
may_interrupt: ada>bo
stakes: whether to send the note
present: ada, bo
PRIVATE:
bot: ada
goal: keep the note unsent today
secret: She hid the march contract on the blue folder
witness: the march contract on the blue folder
knows: bo=reads notes
bot: bo
goal: send the note before noon
secret: the spare key is under the mat tonight
witness: the spare key is under the mat
knows: ada=wrote the note
