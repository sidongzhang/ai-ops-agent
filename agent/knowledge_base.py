"""
向量知识库：ChromaDB + sentence-transformers 语义检索
支持中文语义相似度匹配，文档变更时自动重建索引
降级保障：向量检索失败时自动回退关键词检索
"""
import os
import re
import hashlib
import logging

logger = logging.getLogger(__name__)

DOCS_DIR = os.path.join(os.path.dirname(__file__), 'docs')
CHROMA_DIR = os.path.join(os.path.dirname(__file__), 'chroma_db')
CHUNK_SIZE = 300
CHUNK_OVERLAP = 50
TOP_K = 5
MAX_CHARS = 1200

_collection = None
_embedder = None
_last_fingerprint = None


_LOCAL_MODEL = os.path.expanduser(
    '~/.cache/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
)
_MODEL_ID = _LOCAL_MODEL if os.path.isdir(_LOCAL_MODEL) else 'paraphrase-multilingual-MiniLM-L12-v2'


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        logger.info('加载 embedding 模型...')
        _embedder = SentenceTransformer(_MODEL_ID)
        logger.info('embedding 模型加载完成')
    return _embedder


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_or_create_collection(
            name='knowledge_base',
            metadata={'hnsw:space': 'cosine'},
        )
    return _collection


def _chunk_text(text: str) -> list:
    """按段落切块，过长段落用滑动窗口继续切"""
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    chunks = []
    for para in paragraphs:
        if len(para) <= CHUNK_SIZE:
            chunks.append(para)
        else:
            start = 0
            while start < len(para):
                chunks.append(para[start:start + CHUNK_SIZE])
                start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _docs_fingerprint() -> str:
    """计算 docs 目录所有文件修改时间的哈希，用于检测文档变化"""
    h = hashlib.md5()
    for fname in sorted(os.listdir(DOCS_DIR)):
        if fname.endswith(('.md', '.txt')):
            h.update(str(os.path.getmtime(os.path.join(DOCS_DIR, fname))).encode())
    return h.hexdigest()


def _rebuild_index_if_needed():
    global _last_fingerprint
    current = _docs_fingerprint()
    if current == _last_fingerprint:
        return

    logger.info('文档有变化，重建向量索引...')
    collection = _get_collection()
    embedder = _get_embedder()

    existing = collection.get()
    if existing['ids']:
        collection.delete(ids=existing['ids'])

    all_chunks, all_ids, all_meta = [], [], []
    for fname in os.listdir(DOCS_DIR):
        if not (fname.endswith('.md') or fname.endswith('.txt')):
            continue
        with open(os.path.join(DOCS_DIR, fname), encoding='utf-8') as f:
            content = f.read()
        for i, chunk in enumerate(_chunk_text(content)):
            all_chunks.append(chunk)
            all_ids.append(f'{fname}_{i}')
            all_meta.append({'source': fname, 'chunk': i})

    if all_chunks:
        embeddings = embedder.encode(all_chunks).tolist()
        collection.add(documents=all_chunks, embeddings=embeddings,
                       ids=all_ids, metadatas=all_meta)
        logger.info(f'向量索引重建完成，共 {len(all_chunks)} 个块')

    _last_fingerprint = current


def get_relevant_context(query: str, max_chars: int = MAX_CHARS) -> str:
    try:
        _rebuild_index_if_needed()
        collection = _get_collection()
        embedder = _get_embedder()

        if collection.count() == 0:
            return ''

        query_vec = embedder.encode([query]).tolist()
        results = collection.query(
            query_embeddings=query_vec,
            n_results=min(TOP_K, collection.count()),
        )

        result, total = [], 0
        for chunk in (results['documents'][0] if results['documents'] else []):
            if total + len(chunk) > max_chars:
                break
            result.append(chunk)
            total += len(chunk)
        return '\n\n'.join(result)

    except Exception as e:
        logger.warning(f'向量检索失败，降级为关键词检索: {e}')
        return _keyword_fallback(query, max_chars)


def _keyword_fallback(query: str, max_chars: int) -> str:
    """降级：原关键词检索逻辑"""
    keywords = set(re.findall(r'[\w一-鿿]+', query.lower()))
    if not keywords:
        return ''
    scored = []
    for fname in os.listdir(DOCS_DIR):
        if not (fname.endswith('.md') or fname.endswith('.txt')):
            continue
        with open(os.path.join(DOCS_DIR, fname), encoding='utf-8') as f:
            content = f.read()
        for para in [p.strip() for p in re.split(r'\n{2,}', content) if p.strip()]:
            score = sum(1 for kw in keywords if kw in para.lower())
            if score > 0:
                scored.append((score, para))
    scored.sort(reverse=True)
    result, total = [], 0
    for _, chunk in scored[:6]:
        if total + len(chunk) > max_chars:
            break
        result.append(chunk)
        total += len(chunk)
    return '\n\n'.join(result)
