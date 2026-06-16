"""
简易知识库：关键词检索，无需向量数据库依赖
从 agent/docs/*.md 中按关键词打分，返回最相关段落
"""
import os
import re

DOCS_DIR = os.path.join(os.path.dirname(__file__), 'docs')


def get_relevant_context(query: str, max_chars: int = 1200) -> str:
    keywords = set(re.findall(r'[\w一-鿿]+', query.lower()))
    if not keywords:
        return ''

    scored_chunks = []

    for filename in os.listdir(DOCS_DIR):
        if not (filename.endswith('.md') or filename.endswith('.txt')):
            continue
        filepath = os.path.join(DOCS_DIR, filename)
        with open(filepath, encoding='utf-8') as f:
            content = f.read()

        # 按段落分割
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', content) if p.strip()]
        for para in paragraphs:
            para_lower = para.lower()
            score = sum(1 for kw in keywords if kw in para_lower)
            if score > 0:
                scored_chunks.append((score, para))

    scored_chunks.sort(key=lambda x: x[0], reverse=True)

    result = []
    total = 0
    for _, chunk in scored_chunks[:6]:
        if total + len(chunk) > max_chars:
            break
        result.append(chunk)
        total += len(chunk)

    return '\n\n'.join(result)
