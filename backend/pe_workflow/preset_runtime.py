"""Fresh import paths and runtime identity for the project-owned DSH tool."""
import hashlib


def tool_hash(source):
    return hashlib.sha256(source).hexdigest()


def tool_filename(source):
    return 'flux-design-tools-'+tool_hash(source)[:20]+'.js'


def render_prompt(prompt,source):
    # Configuration reads text with universal newlines; installation reads bytes.
    # Canonicalize here so Windows CRLF cannot create a false version mismatch.
    return prompt.replace('\r\n','\n').replace('\r','\n').replace('./flux-design-tools.js','./'+tool_filename(source))
