#!usrbinenv python3
Follow a grok headless log and collect messages while the process runs.

Reads newline-delimited JSON. Two shapes are accepted

- streaming-json (the one to request) thought, tool_call, tool_call_update,
  text, plan, usage, end, error. Switch on type. Unknown types are skipped.
- streaming-messages-json, including a log that also has stream_event lines.
  stream_event is a token fragment. It is not a message.

Writes, atomically
- status.txt     four lines for a progress question
- messages.jsonl one record per tool, text, result, or error
- session        session id, once seen
- output.md      final text, only if that file is missing or empty when the run ends


import argparse
import json
import os
import time


def short(value, limit=110)
    text =  .join(str(value).split())
    if len(text) = limit
        return text
    return text[ limit - 1] + …


def hint(raw)
    if not isinstance(raw, dict)
        return short(raw, 80)
    for key in (path, file_path, url, command, query, pattern, prompt)
        if raw.get(key)
            return short(raw[key], 80)
    return short(json.dumps(raw, ensure_ascii=False), 80)


class State
    def __init__(self, started)
        self.started = started
        self.phase = Starting
        self.action = launched
        self.done = False
        self.error = False
        self.session = None
        self.text_parts = []
        self.final_text = 
        self.duration_ms = None

    def elapsed(self)
        if self.duration_ms
            minutes = max(1, int(self.duration_ms  60000)) if self.duration_ms = 60000 else 0
            if minutes == 0
                return f{max(1, int(self.duration_ms  1000))} s
            return f{minutes} min
        minutes = int((time.time() - self.started)  60)
        return f{minutes} min

    def lines(self)
        if self.error
            head = Finished.
        elif self.done
            head = Finished.
        else
            head = Running.
        return f{head}n{self.elapsed()}.n{self.phase}.nLast action {self.action}n


def flush_text(state, sink)
    if not state.text_parts
        return
    text = .join(state.text_parts)
    state.text_parts = []
    if text.strip()
        state.final_text = text
        sink({kind text, text text})


def on_tool(state, sink, name, raw, status)
    state.phase = name or tool
    state.action = f{state.phase} {hint(raw)}.strip()
    sink({kind tool, name state.phase, status status or in_progress, action state.action})


def handle(obj, state, sink)
    kind = obj.get(type)
    if kind == stream_event
        return
    if kind == thought
        state.phase = Thinking
        return
    if kind == plan
        for entry in obj.get(entries) or []
            if not isinstance(entry, dict)
                continue
            if entry.get(status) in (in_progress, pending)
                label = entry.get(content) or entry.get(title) or entry.get(step)
                if label
                    state.phase = short(label, 60)
                return
        return
    if kind == tool_call
        on_tool(state, sink, obj.get(toolName) or obj.get(title) or tool, obj.get(rawInput), obj.get(status))
        return
    if kind == tool_call_update
        status = obj.get(status) or update
        state.action = f{state.phase} {status}
        sink({kind tool_update, status status, action state.action})
        return
    if kind == text
        data = obj.get(data) or 
        state.text_parts.append(data)
        state.phase = Writing
        if data.strip()
            state.action = short(data, 110)
        return
    if kind == usage
        flush_text(state, sink)
        return
    if kind == end
        flush_text(state, sink)
        state.done = True
        state.phase = Done
        state.session = obj.get(sessionId) or obj.get(session_id) or state.session
        state.action = process exited
        sink({kind end, stop obj.get(stopReason)})
        return
    if kind == error
        state.done = True
        state.error = True
        state.phase = Error
        state.action = short(obj.get(message) or error, 140)
        sink({kind error, message state.action})
        return
    if kind == system
        state.session = obj.get(session_id) or state.session
        return
    if kind == assistant
        state.session = obj.get(session_id) or state.session
        content = (obj.get(message) or {}).get(content) or []
        for block in content
            if not isinstance(block, dict)
                continue
            block_type = block.get(type)
            if block_type == thinking
                state.phase = Thinking
            elif block_type == text
                text = block.get(text) or 
                state.phase = Writing
                state.final_text = text
                state.action = short(text, 110)
                sink({kind text, text text})
            elif block_type == tool_use
                on_tool(state, sink, block.get(name) or tool, block.get(input), in_progress)
        return
    if kind == user
        content = (obj.get(message) or {}).get(content) or []
        for block in content
            if isinstance(block, dict) and block.get(type) == tool_result
                failed = bool(block.get(is_error))
                if failed
                    state.action = tool error
                elif state.phase not in (Writing, Thinking, Starting, Done)
                    state.action = f{state.phase} done
                else
                    state.action = tool result
                sink({kind tool_result, error failed, action state.action})
        return
    if kind == result
        flush_text(state, sink)
        state.done = True
        state.error = bool(obj.get(is_error))
        state.phase = Error if state.error else Done
        state.session = obj.get(session_id) or state.session
        state.duration_ms = obj.get(duration_ms) or state.duration_ms
        result = obj.get(result) or 
        if result
            state.final_text = result
            state.action = short(result, 110)
        else
            state.action = process exited
        sink({kind result, error state.error, stop obj.get(stop_reason), text result})


def atomic_write(path, text)
    temporary = path + .tmp
    with open(temporary, w, encoding=utf-8) as handle
        handle.write(text)
    os.replace(temporary, path)


def pid_alive(pid_file)
    if not pid_file or not os.path.exists(pid_file)
        return True
    try
        pid = int(open(pid_file, encoding=utf-8).read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError)
        return False
    return True


def consume(path, position, pending, state, sink)
    if not os.path.exists(path)
        return position, pending
    with open(path, r, encoding=utf-8, errors=replace) as source
        source.seek(position)
        chunk = source.read()
        position = source.tell()
    if not chunk
        return position, pending
    pending += chunk
    while n in pending
        line, pending = pending.split(n, 1)
        line = line.strip()
        if not line.startswith({)
            continue
        try
            obj = json.loads(line)
        except json.JSONDecodeError
            continue
        if isinstance(obj, dict)
            handle(obj, state, sink)
    return position, pending


def publish(args, state)
    atomic_write(args.out, state.lines())
    if state.session
        atomic_write(args.session, state.session + n)
    if not state.done or not state.final_text
        return
    if os.path.exists(args.output) and open(args.output, encoding=utf-8).read().strip()
        return
    atomic_write(args.output, state.final_text)


def main()
    parser = argparse.ArgumentParser()
    parser.add_argument(--log, required=True)
    parser.add_argument(--out, required=True)
    parser.add_argument(--messages, required=True)
    parser.add_argument(--session, required=True)
    parser.add_argument(--output, required=True)
    parser.add_argument(--pid-file)
    parser.add_argument(--once, action=store_true)
    args = parser.parse_args()

    state = State(time.time())
    written = 0
    records = []

    def sink(record)
        records.append(record)

    position = 0
    pending = 
    drained = False
    while True
        position, pending = consume(args.log, position, pending, state, sink)
        if len(records)  written
            with open(args.messages, a, encoding=utf-8) as handle
                for record in records[written]
                    handle.write(json.dumps(record, ensure_ascii=False) + n)
            written = len(records)
        publish(args, state)
        if args.once
            return
        alive = pid_alive(args.pid_file)
        if not alive and drained
            if not state.done
                state.done = True
                state.action = state.action if state.action != launched else process exited
                publish(args, state)
            return
        if not alive
            drained = True
        time.sleep(0.5 if not alive else 1)


if __name__ == __main__
    main()