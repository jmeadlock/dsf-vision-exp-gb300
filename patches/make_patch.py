#!/usr/bin/env python3
"""Patch encoding_dsv4.py: replace encode_arguments_to_dsml with the upstream-main version
that accepts dict OR str arguments (sgl-project/sglang #28035 second half).

Usage: python3 make_patch.py <in.py> <out.py>
"""
import re, sys

src = open(sys.argv[1]).read()

OLD_HEAD = 'def encode_arguments_to_dsml(tool_call: Dict[str, str]) -> str:'
assert src.count(OLD_HEAD) == 1, "function header not found exactly once"
start = src.index(OLD_HEAD)
# function ends at the next top-level 'def '
m = re.search(r'\n(?=def |class )', src[start + 1:])
end = start + 1 + m.start()
old_fn = src[start:end]
assert 'except Exception as err:' in old_fn and '{"arguments": tool_call["arguments"]}' in old_fn, \
    "did not find the buggy fallback; refusing to patch"

NEW_FN = '''def encode_arguments_to_dsml(tool_call: Dict[str, str]) -> str:
    """
    Encode tool call arguments into DSML parameter format.

    Args:
        tool_call: Dict with "name" and "arguments" keys.

    Returns:
        DSML-formatted parameter string.
    """
    p_dsml_template = '<{dsml_token}parameter name="{key}" string="{is_str}">{value}</{dsml_token}parameter>'
    P_dsml_strs = []

    # PATCHED (jmeadlock, 2026-09-03): serving_chat.py already normalises history
    # tool-call arguments str -> dict (upstream #28035). The preview image shipped
    # the pre-#28035 encoder, whose json.loads(dict) TypeError fell through to
    # {"arguments": <dict>} and rendered every prior call as one parameter named
    # "arguments" -> the model imitates it and nests deeper each turn.
    raw_arguments = tool_call["arguments"]
    arguments = (
        json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    )
    if not isinstance(arguments, dict):
        raise ValueError(
            "Assistant tool call function.arguments must be a JSON object."
        )

    for k, v in arguments.items():
        p_dsml_str = p_dsml_template.format(
            dsml_token=dsml_token,
            key=k,
            is_str="true" if isinstance(v, str) else "false",
            value=v if isinstance(v, str) else to_json(v),
        )
        P_dsml_strs.append(p_dsml_str)

    return "\\n".join(P_dsml_strs)

'''
out = src[:start] + NEW_FN + src[end:]
open(sys.argv[2], 'w').write(out)
print(f"patched: {len(old_fn)} -> {len(NEW_FN)} chars; total {len(src)} -> {len(out)}")
