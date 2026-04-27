# -*- coding: utf-8 -*-
"""
kb-builder 一键初始化脚本

按顺序执行完整的数据管线：
  1. 环境配置（uv sync）
  2. 大表 SQLite 索引（build_index.py）
  3. xlsx/docx → markdown 缓存（batch_convert.py）
  4. Wiki 知识库编译（wiki_compiler.py）

懒加载：每一步都检查前置产物的时效性，已完成的步骤自动跳过。

用法：
    python init.py                          # 一键初始化（懒加载）
    python init.py -f                       # 强制全量重建所有步骤
    python init.py --skip-index             # 跳过索引构建
    python init.py --skip-convert           # 跳过 markdown 转换
    python init.py --skip-wiki              # 跳过 wiki 编译
    python init.py --knowledge /path/to/kb  # 自定义知识库路径
    python init.py --threshold 500          # 自定义大表阈值 (KB)
    python init.py --open                   # 编译完成后打开知识图谱
"""

import os
import sys

# 加载 .env 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import json
import time
import shutil
import subprocess
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 默认路径（相对于项目根目录）
DEFAULT_KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, 'knowledge')
DEFAULT_GAMEDATA_DIR = os.path.join(DEFAULT_KNOWLEDGE_DIR, 'gamedata')
DEFAULT_GAMEDOCS_DIR = os.path.join(DEFAULT_KNOWLEDGE_DIR, 'gamedocs')
DEFAULT_INDEX_DB = os.path.join(DEFAULT_KNOWLEDGE_DIR, 'index.db')
DEFAULT_WIKI_DIR = os.path.join(DEFAULT_KNOWLEDGE_DIR, 'wiki')
DEFAULT_GRAPH_DIR = os.path.join(DEFAULT_KNOWLEDGE_DIR, 'graph')

# 大表阈值 KB
DEFAULT_THRESHOLD_KB = 200


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

class StepResult:
    """步骤执行结果。"""
    def __init__(self, name, status, elapsed=0, detail=''):
        self.name = name
        self.status = status  # 'ok', 'skip', 'fail'
        self.elapsed = elapsed
        self.detail = detail

    def __repr__(self):
        icon = {'ok': '✓', 'skip': '○', 'fail': '✗'}[self.status]
        return f"  [{icon}] {self.name} ({self.elapsed:.1f}s) {self.detail}"


def _run_cmd(cmd, cwd=None, check=True):
    """运行子进程命令，返回 (returncode, stdout, stderr)。"""
    result = subprocess.run(
        cmd,
        cwd=cwd or SCRIPT_DIR,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"命令失败: {' '.join(cmd)}\n"
            f"  退出码: {result.returncode}\n"
            f"  stderr: {result.stderr[:500]}"
        )
    return result.returncode, result.stdout, result.stderr


def _has_uv():
    """检查 uv 是否可用。"""
    return shutil.which('uv') is not None


def _count_cache_files(directory):
    """递归统计 .cache/ 目录下的 .md 文件数量。"""
    count = 0
    if not os.path.isdir(directory):
        return count
    for root, dirs, files in os.walk(directory):
        # 只统计 .cache 子目录
        if os.path.basename(root) == '.cache':
            count += sum(1 for f in files if f.endswith('.md'))
    return count


def _count_source_files(directory, extensions):
    """递归统计指定扩展名的源文件数量。"""
    count = 0
    if not os.path.isdir(directory):
        return count
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if any(f.lower().endswith(ext) for ext in extensions):
                if not f.startswith('~'):
                    count += 1
    return count


def _count_xlsx_files(gamedata_dir):
    """统计 gamedata 目录下的 xlsx 文件数量。"""
    return _count_source_files(gamedata_dir, ['.xlsx'])


def _count_docx_files(gamedocs_dir):
    """统计 gamedocs 目录下的 docx 文件数量。"""
    return _count_source_files(gamedocs_dir, ['.docx'])


def _index_is_fresh(db_path, gamedata_dir, threshold):
    """检查 SQLite 索引是否需要更新。

    逻辑：如果数据库存在，且所有大表的 file_hash 与文件系统一致，则认为新鲜。
    """
    if not os.path.exists(db_path):
        return False

    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        # 检查 _tables 表是否存在
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_tables'"
        ).fetchone()
        if not tables:
            conn.close()
            return False

        # 读取已索引的表
        indexed = {}
        for row in conn.execute("SELECT table_name, file_hash, file_size FROM _tables"):
            indexed[row[0]] = (row[1], row[2])
        conn.close()

        if not indexed:
            return False

        # 扫描当前大表
        from build_index import _scan_xlsx_files, _file_hash
        big_files = _scan_xlsx_files(gamedata_dir, threshold)

        # 数量必须一致
        if len(big_files) != len(indexed):
            return False

        # 每张表的 hash 必须一致
        for table_name, filepath, file_size in big_files:
            if table_name not in indexed:
                return False
            stored_hash, stored_size = indexed[table_name]
            if stored_size != file_size:
                return False
            if stored_hash != _file_hash(filepath):
                return False

        return True

    except Exception:
        return False


def _convert_is_fresh(knowledge_dir):
    """检查 markdown 缓存是否需要更新。

    逻辑：比较 .cache/*.md 文件数量与源文件数量，如果缓存覆盖率高则跳过。
    """
    gamedata_dir = os.path.join(knowledge_dir, 'gamedata')
    gamedocs_dir = os.path.join(knowledge_dir, 'gamedocs')

    # 统计源文件
    xlsx_count = _count_xlsx_files(gamedata_dir)
    docx_count = _count_docx_files(gamedocs_dir)
    total_source = xlsx_count + docx_count

    if total_source == 0:
        return True  # 没有源文件，无需转换

    # 统计缓存
    cache_count = 0
    for subdir in [gamedata_dir, gamedocs_dir]:
        if os.path.isdir(subdir):
            for root, dirs, files in os.walk(subdir):
                if '.cache' in dirs:
                    cache_dir = os.path.join(root, '.cache')
                    cache_count += sum(
                        1 for f in os.listdir(cache_dir)
                        if f.endswith('.md')
                    )

    # 覆盖率 > 90% 则认为新鲜
    coverage = cache_count / total_source if total_source > 0 else 1.0
    return coverage > 0.9


def _wiki_is_fresh(knowledge_dir):
    """检查 wiki 产物是否需要更新。"""
    wiki_dir = os.path.join(knowledge_dir, 'wiki')
    if not os.path.isdir(wiki_dir):
        return False

    # 检查关键产物是否存在
    required = ['index.md', 'concepts.md', 'lint_report.md']
    return all(os.path.exists(os.path.join(wiki_dir, f)) for f in required)


# ══════════════════════════════════════════════════════════════
# 步骤实现
# ══════════════════════════════════════════════════════════════

def step_install_deps(force=False):
    """Step 1: 安装 Python 依赖。"""
    t0 = time.time()

    venv_dir = os.path.join(SCRIPT_DIR, '.venv')
    if not force and os.path.isdir(venv_dir):
        # 简单检查：如果 .venv 存在且 markitdown 可导入，则跳过
        try:
            _, out, _ = _run_cmd(
                [sys.executable, '-c', 'import markitdown; print("ok")'],
                check=False
            )
            if 'ok' in out:
                return StepResult('环境配置', 'skip', time.time() - t0, '依赖已安装')
        except Exception:
            pass

    if not _has_uv():
        return StepResult('环境配置', 'fail', time.time() - t0, 'uv 未安装，请先安装: https://docs.astral.sh/uv/')

    try:
        _run_cmd(['uv', 'sync'], cwd=SCRIPT_DIR)
        return StepResult('环境配置', 'ok', time.time() - t0, '依赖安装完成')
    except Exception as e:
        return StepResult('环境配置', 'fail', time.time() - t0, str(e)[:200])


def step_build_index(gamedata_dir, db_path, threshold_kb, force=False):
    """Step 2: 构建大表 SQLite 索引。"""
    t0 = time.time()
    threshold = threshold_kb * 1024

    if not os.path.isdir(gamedata_dir):
        return StepResult('大表索引', 'skip', time.time() - t0, f'{gamedata_dir} 不存在')

    if not force and _index_is_fresh(db_path, gamedata_dir, threshold):
        # 统计已有索引
        import sqlite3
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM _tables").fetchone()[0]
        rows = conn.execute("SELECT SUM(row_count) FROM _tables").fetchone()[0] or 0
        conn.close()
        return StepResult('大表索引', 'skip', time.time() - t0,
                          f'索引已是最新 ({count} 表, {rows:,} 行)')

    try:
        from build_index import build_index
        build_index(gamedata_dir, db_path, threshold=threshold, force=force)

        # 读取统计
        import sqlite3
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM _tables").fetchone()[0]
        rows = conn.execute("SELECT SUM(row_count) FROM _tables").fetchone()[0] or 0
        conn.close()

        db_size = os.path.getsize(db_path) / 1024 / 1024
        return StepResult('大表索引', 'ok', time.time() - t0,
                          f'{count} 表, {rows:,} 行 ({db_size:.0f} MB)')
    except Exception as e:
        return StepResult('大表索引', 'fail', time.time() - t0, str(e)[:200])


def step_batch_convert(knowledge_dir, force=False):
    """Step 3: 批量转换 xlsx/docx → markdown 缓存。"""
    t0 = time.time()

    gamedata_dir = os.path.join(knowledge_dir, 'gamedata')
    gamedocs_dir = os.path.join(knowledge_dir, 'gamedocs')

    xlsx_count = _count_xlsx_files(gamedata_dir)
    docx_count = _count_docx_files(gamedocs_dir)

    if xlsx_count + docx_count == 0:
        return StepResult('Markdown 转换', 'skip', time.time() - t0, '没有源文件')

    if not force and _convert_is_fresh(knowledge_dir):
        cache_count = 0
        for subdir in [gamedata_dir, gamedocs_dir]:
            if os.path.isdir(subdir):
                for root, dirs, files in os.walk(subdir):
                    if '.cache' in dirs:
                        cache_dir = os.path.join(root, '.cache')
                        cache_count += sum(1 for f in os.listdir(cache_dir) if f.endswith('.md'))
        return StepResult('Markdown 转换', 'skip', time.time() - t0,
                          f'缓存已是最新 ({cache_count} 文件)')

    try:
        from batch_convert import batch_convert
        batch_convert(knowledge_dir, force=force)

        cache_count = 0
        for subdir in [gamedata_dir, gamedocs_dir]:
            if os.path.isdir(subdir):
                for root, dirs, files in os.walk(subdir):
                    if '.cache' in dirs:
                        cache_dir = os.path.join(root, '.cache')
                        cache_count += sum(1 for f in os.listdir(cache_dir) if f.endswith('.md'))

        return StepResult('Markdown 转换', 'ok', time.time() - t0,
                          f'{xlsx_count} xlsx + {docx_count} docx → {cache_count} 缓存')
    except Exception as e:
        return StepResult('Markdown 转换', 'fail', time.time() - t0, str(e)[:200])


def step_compile_wiki(knowledge_dir, force=False, skip_graph=False, no_infer=False, open_graph=False):
    """Step 4: 编译 Wiki 知识库。"""
    t0 = time.time()

    if not force and _wiki_is_fresh(knowledge_dir):
        wiki_dir = os.path.join(knowledge_dir, 'wiki')
        files = os.listdir(wiki_dir) if os.path.isdir(wiki_dir) else []
        md_count = sum(1 for f in files if f.endswith('.md'))
        entity_dir = os.path.join(wiki_dir, 'entities')
        entity_count = len(os.listdir(entity_dir)) if os.path.isdir(entity_dir) else 0
        return StepResult('Wiki 编译', 'skip', time.time() - t0,
                          f'wiki 已是最新 ({md_count} 文件, {entity_count} entity 组)')

    try:
        from wiki_compiler import compile_wiki
        stats = compile_wiki(
            cache_dir=os.path.join(knowledge_dir, 'gamedocs', '.cache'),
            knowledge_dir=knowledge_dir,
            skip_graph=skip_graph,
            no_infer=no_infer,
        )

        # 如果需要打开图谱
        if open_graph and stats.get('graph', {}).get('html_path'):
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(stats['graph']['html_path'])}")

        detail_parts = []
        if 'entities' in stats:
            detail_parts.append(f"{stats['entities']} entities")
        if 'concepts' in stats:
            detail_parts.append(f"{stats['concepts']} concepts")
        if 'lint_unknown' in stats:
            detail_parts.append(f"{stats['lint_unknown']} unknown")

        return StepResult('Wiki 编译', 'ok', time.time() - t0,
                          ', '.join(detail_parts) if detail_parts else '完成')
    except Exception as e:
        return StepResult('Wiki 编译', 'fail', time.time() - t0, str(e)[:200])


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def run_init(knowledge_dir=None, threshold_kb=DEFAULT_THRESHOLD_KB,
             force=False, skip_index=False, skip_convert=False,
             skip_wiki=False, skip_graph=False, open_graph=False, no_infer=False):
    """执行完整初始化流程。"""
    knowledge_dir = knowledge_dir or DEFAULT_KNOWLEDGE_DIR
    gamedata_dir = os.path.join(knowledge_dir, 'gamedata')
    db_path = os.path.join(knowledge_dir, 'index.db')

    print("=" * 60)
    print("  kb-builder 初始化")
    print("=" * 60)
    print(f"  知识库: {knowledge_dir}")
    print(f"  大表阈值: {threshold_kb} KB")
    print(f"  强制重建: {'是' if force else '否'}")
    print()

    results = []
    total_t0 = time.time()

    # Step 1: 环境配置
    print("[1/4] 环境配置")
    r = step_install_deps(force=force)
    results.append(r)
    print(r)
    if r.status == 'fail':
        print("\n环境配置失败，中止。请先安装 uv: https://docs.astral.sh/uv/")
        return results

    # Step 2: 大表索引
    print("\n[2/4] 大表 SQLite 索引")
    if skip_index:
        r = StepResult('大表索引', 'skip', 0, '已跳过 (--skip-index)')
    else:
        r = step_build_index(gamedata_dir, db_path, threshold_kb, force=force)
    results.append(r)
    print(r)

    # Step 3: Markdown 转换
    print("\n[3/4] Markdown 转换")
    if skip_convert:
        r = StepResult('Markdown 转换', 'skip', 0, '已跳过 (--skip-convert)')
    else:
        r = step_batch_convert(knowledge_dir, force=force)
    results.append(r)
    print(r)

    # Step 4: Wiki 编译
    print("\n[4/4] Wiki 编译")
    if skip_wiki:
        r = StepResult('Wiki 编译', 'skip', 0, '已跳过 (--skip-wiki)')
    else:
        r = step_compile_wiki(knowledge_dir, force=force,
                              open_graph=open_graph, skip_graph=skip_graph,
                              no_infer=no_infer)
    results.append(r)
    print(r)

    # 汇总
    total_elapsed = time.time() - total_t0
    ok = sum(1 for r in results if r.status == 'ok')
    skip = sum(1 for r in results if r.status == 'skip')
    fail = sum(1 for r in results if r.status == 'fail')

    print(f"\n{'='*60}")
    print(f"  初始化完成  耗时 {total_elapsed:.1f}s")
    print(f"  执行: {ok}  跳过: {skip}  失败: {fail}")
    print(f"{'='*60}")

    # 产物路径提示
    print("\n产物路径:")
    if os.path.exists(db_path):
        db_size = os.path.getsize(db_path) / 1024 / 1024
        print(f"  SQLite 索引: {db_path} ({db_size:.0f} MB)")
    wiki_dir = os.path.join(knowledge_dir, 'wiki')
    if os.path.isdir(wiki_dir):
        print(f"  Wiki 知识库: {wiki_dir}")
    graph_dir = os.path.join(knowledge_dir, 'graph')
    graph_html = os.path.join(graph_dir, 'graph.html')
    if os.path.exists(graph_html):
        print(f"  知识图谱:    {graph_html}")

    return results


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='kb-builder 一键初始化',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python init.py                          # 一键初始化（懒加载）
  python init.py -f                       # 强制全量重建
  python init.py --skip-index             # 跳过索引构建
  python init.py --skip-convert           # 跳过 markdown 转换
  python init.py --skip-wiki              # 跳过 wiki 编译
  python init.py --knowledge /path/to/kb  # 自定义知识库路径
  python init.py --threshold 500          # 大表阈值 500KB
  python init.py --open                   # 编译后打开知识图谱
        """)
    parser.add_argument('-f', '--force', action='store_true',
                        help='强制全量重建（忽略增量检测）')
    parser.add_argument('--knowledge', type=str, default=DEFAULT_KNOWLEDGE_DIR,
                        help=f'知识库根目录 (默认: knowledge/)')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD_KB,
                        help=f'大表阈值 KB (默认: {DEFAULT_THRESHOLD_KB})')
    parser.add_argument('--skip-index', action='store_true',
                        help='跳过 SQLite 索引构建')
    parser.add_argument('--skip-convert', action='store_true',
                        help='跳过 markdown 转换')
    parser.add_argument('--skip-wiki', action='store_true',
                        help='跳过 wiki 编译')
    parser.add_argument('--skip-graph', action='store_true',
                        help='跳过知识图谱构建（加速 wiki 编译）')
    parser.add_argument('--open', action='store_true',
                        help='编译完成后在浏览器中打开知识图谱')
    parser.add_argument('--no-infer', action='store_true',
                        help='跳过 LLM 语义推理（加速编译）')

    args = parser.parse_args()

    results = run_init(
        knowledge_dir=os.path.abspath(args.knowledge),
        threshold_kb=args.threshold,
        force=args.force,
        skip_index=args.skip_index,
        skip_convert=args.skip_convert,
        skip_wiki=args.skip_wiki,
        skip_graph=args.skip_graph,
        open_graph=args.open,
        no_infer=args.no_infer,
    )

    # 如果有失败的步骤，退出码为 1
    if any(r.status == 'fail' for r in results):
        sys.exit(1)
