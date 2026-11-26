"""Read Instruments XML references and count interval unions without overlap inflation."""

import re
import xml.etree.ElementTree as ET


def union_ns(intervals):
    total, end = 0, None
    for start, stop in sorted((a, b) for a, b in intervals if b > a):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def overlap_ns(intervals, start, stop):
    return union_ns([(max(a, start), min(b, stop)) for a, b in intervals if b > start and a < stop])


class Table:
    def __init__(self, path):
        self.tree = ET.parse(path)
        self.ids = {node.get("id"): node for node in self.tree.iter() if node.get("id")}
        schema = self.tree.find(".//schema")
        self.columns = [col.findtext("mnemonic") for col in schema.findall("col")] if schema is not None else []
        self.rows = self.tree.findall(".//row")

    def resolve(self, node):
        seen = set()
        while node is not None and node.get("ref"):
            ref = node.get("ref")
            if ref in seen:
                return None
            seen.add(ref)
            node = self.ids.get(ref)
        return node

    def text(self, node):
        node = self.resolve(node)
        return "" if node is None else (node.get("fmt") or node.text or "")

    def number(self, node):
        node = self.resolve(node)
        if node is None:
            return None
        value = (node.text or "").strip()
        return int(value) if re.fullmatch(r"-?\d+", value) else None

    def fields(self, row):
        return dict(zip(self.columns, list(row)))

    def pid(self, node):
        node = self.resolve(node)
        if node is None:
            return None
        for part in node.iter():
            if part.tag in ("pid", "process-id"):
                number = self.number(part)
                if number is not None:
                    return number
        text = self.text(node)
        match = re.search(r"\bpid\s*:\s*(\d+)|\((\d+)\)\s*$", text)
        return int(next(group for group in match.groups() if group)) if match else None

    def frames(self, node):
        node = self.resolve(node)
        if node is None:
            return []
        names = []
        for candidate in node.iter():
            resolved = self.resolve(candidate)
            if resolved is not None and resolved.tag == "frame":
                name = resolved.get("name") or resolved.get("fmt") or resolved.findtext("name")
                if name:
                    names.append(name)
        return names
