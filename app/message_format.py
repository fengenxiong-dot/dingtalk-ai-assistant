"""DingTalk supported Markdown subset and bounded, balanced message chunks."""
import re


def normalize(text):
    """Repair escaped emphasis and Chinese punctuation delimiter boundaries.

    Keep fenced and inline code verbatim, where stars/backslashes are data.
    """
    pieces = re.split(r"(```[\s\S]*?```|`[^`\n]*`)", text)
    for index in range(0, len(pieces), 2):
        value = pieces[index].replace(r"\*\*", "**")
        value = re.sub(r"\*\*([^\n]+?)\*\*", lambda match: " **" + match[1] + "** ", value)
        pieces[index] = value
    return "".join(pieces)


def has_markdown(text):
    return bool(re.search(r"(?m)^#{1,6}\s|\*\*.+?\*\*|^[-*]\s|^\d+\.\s|^>\s|```|\[[^\]]+\]\(https?://", text))


def chunks(text, budget=2800):
    # Split on lines whenever possible; preserve fenced blocks across messages.
    result, part, fence = [], "", False
    for line in text.splitlines(keepends=True):
        fragments, current = [], ""
        for char in line:
            if len((current + char).encode()) > budget - 32:
                fragments.append(current)
                current = ""
            current += char
        fragments.append(current)
        for fragment in fragments:
            if part and len((part + fragment).encode()) > budget - 32:
                result.append(part + ("\n```" if fence else ""))
                part = "```\n" if fence else ""
            part += fragment
            if fragment.lstrip().startswith("```"):
                fence = not fence
    if part:
        result.append(part + ("\n```" if fence else ""))
    # An overlong inline emphasis can cross a split. Prefer plain readable text
    # for those chunks instead of leaking unmatched formatting markers.
    return [value.replace("**", "") if value.count("**") % 2 else value for value in result]
