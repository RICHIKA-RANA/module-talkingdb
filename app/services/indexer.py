import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

# Called as ``progress(done_units, total_units)`` during indexing.
# Any exception raised by the callback aborts indexing immediately.
ProgressCallback = Callable[[int, int], None]

from tqdm import tqdm

from talkingdb.models.document.document import DocumentModel
from talkingdb.models.document.elements.primitive.paragraph import ParagraphModel
from talkingdb.models.document.elements.primitive.table import TableModel, TableRowView
from talkingdb.models.document.indexes.index import (
    FileIndexModel,
    IndexItem,
    IndexType,
)
from talkingdb.models.graph.graph import GraphModel
from app.services.package_text_tokenizer import TextTokenizer
from app.services.package_symbol_generator import SymbolGenerator
from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.logger.console import logger
from app.core import config
from app.services.job_context import JobCancelled

Nodes = List[Tuple[str, Dict[str, Any]]]
Edges = List[Tuple[str, str, Dict[str, Any]]]

CONTEXT_EDGE = "context"
CONTAINS_EDGE = "contains"


class IndexerService:
    def __init__(self, max_workers: int | None = None):
        self.gm = GraphModel.create(GraphModel.make_id(uuid4().hex), True)
        self.tokenizer = TextTokenizer()
        self.symbol_generator = SymbolGenerator()
        self.max_workers = (
            max_workers if max_workers is not None else config.INDEXER_MAX_WORKERS
        )

    def graph_file_index(
        self,
        file_index: FileIndexModel,
        progress: Optional[ProgressCallback] = None,
    ) -> GraphModel:
        """Build the graph's tree-of-headings structure."""

        def count_nodes(node: IndexItem) -> int:
            return 1 + sum(count_nodes(child) for child in node.child)

        total = sum(count_nodes(top) for top in file_index.nodes)
        done = 0

        if progress is not None:
            progress(0, total)

        def walk(node: IndexItem, parent_id: str = None):
            nonlocal done

            self.gm.graph.add_node(node.id, label=node.label, index=node.index)

            if parent_id:
                self.gm.graph.add_edge(parent_id, node.id, type="part_of")

            done += 1
            if progress is not None:
                progress(done, total)

            for child in node.child:
                walk(child, node.id)

        self.gm.graph.add_node(
            file_index.id,
            label=getattr(file_index, "filename", None),
            index="file@root",
        )

        for top_node in file_index.nodes:
            walk(top_node, file_index.id)

        with sqlite_conn(GRAPH_DB) as conn:
            self.gm.save(conn)

        return self.gm

    # ───────────────────────────────── symbols ─────────────────────────────────

    def _symbols(self, text: str, structured: bool = False) -> List[Tuple[str, str]]:
        """Tokenize text into (symbol, gram_type) pairs.

        When ``structured=True``, preserves numbers and stopwords for table
        content only, enabling table retrieval without affecting paragraph ranking.
        """
        if not text:
            return []

        token_sets = [self.tokenizer.tokenize(text)]

        if structured:
            token_sets.append(self.tokenizer.tokenize_structured(text))

        return [
            (symbol, gram)
            for tokens in token_sets
            for gram, symbols in self.symbol_generator.generate(tokens).items()
            for symbol in symbols
        ]

    @staticmethod
    def _symbol_edges(
        owner_id: str,
        symbols: List[Tuple[str, str]],
        edge_type: str = CONTAINS_EDGE,
    ) -> Tuple[Nodes, Edges]:
        """Connect symbols to their owning nodes.

        This makes table content reachable through symbol-based graph traversal.
        """
        nodes = [(symbol, {"type": gram}) for symbol, gram in symbols]
        edges = [(owner_id, symbol, {"type": edge_type}) for symbol, _ in symbols]
        return nodes, edges

    # ───────────────────────────────── elements ─────────────────────────────────

    def _process_paragraph(self, document: DocumentModel, element: ParagraphModel):
        node_id = element.id
        text = element.to_text()

        metadata = {
            "index": IndexType.PARA,
            "heading_path": document._get_heading_path(element),
            "filename": document.filename,
            "page": element.page,
        }

        nodes: Nodes = [
            (node_id, {"text": text, "metadata": metadata, "type": "paragraph"})
        ]
        edges: Edges = []

        sym_nodes, sym_edges = self._symbol_edges(node_id, self._symbols(text))
        nodes.extend(sym_nodes)
        edges.extend(sym_edges)

        for line in text.splitlines():
            line = line.strip()
            if ":" not in line:
                continue

            key_raw, val_raw = [part.strip() for part in line.split(":", 1)]
            if not key_raw or not val_raw:
                continue

            key_id = f"key::{self.symbol_generator.max_gram(self.tokenizer.tokenize(key_raw))}"
            val_id = f"val::{self.symbol_generator.max_gram(self.tokenizer.tokenize(val_raw, False))}"

            nodes.append((key_id, {"text": key_raw, "type": "key"}))
            nodes.append((val_id, {"text": val_raw, "type": "value"}))

            edges.append((key_id, val_id, {"type": "key_value"}))
            edges.append((node_id, key_id, {"type": CONTAINS_EDGE}))
            edges.append((node_id, val_id, {"type": "describes"}))

        return nodes, edges

    def _process_table(self, document: DocumentModel, element: TableModel):
        table_id = element.id

        context = document.get_table_context(element)
        context += list(element.column_headers().values())

        metadata = {
            "index": IndexType.TABLE,
            "heading_path": document._get_heading_path(element),
            "filename": document.filename,
            "page": element.page,
        }

        nodes: Nodes = [
            (
                table_id,
                {"text": element.to_html(), "metadata": metadata, "type": "table"},
            )
        ]

        sym_nodes, edges = self._symbol_edges(
            table_id,
            self._symbols(" ".join(context), structured=True),
            CONTEXT_EDGE,
        )
        nodes.extend(sym_nodes)

        return nodes, edges

    def _process_table_row(
        self,
        table_id: str,
        row: TableRowView,
        metadata: Dict[str, Any],
        context_symbols: List[Tuple[str, str]],
    ):
        row_text = row.to_text()
        if not row_text:
            return [], []

        row_id = f"{table_id}:row::{row.index}"

        nodes: Nodes = [
            (
                row_id,
                {
                    "text": row_text,
                    "type": "table_row",
                    "metadata": {
                        **metadata,
                        "index": IndexType.TABLE_ROW,
                        "table_id": table_id,
                        "row_header": row.header,
                        "col_headers": [c.col_header for c in row.cells],
                    },
                },
            )
        ]
        edges: Edges = [(table_id, row_id, {"type": "part_of"})]

        sym_nodes, sym_edges = self._symbol_edges(row_id, context_symbols, CONTEXT_EDGE)
        nodes.extend(sym_nodes)
        edges.extend(sym_edges)

        sym_nodes, sym_edges = self._symbol_edges(
            row_id, self._symbols(row_text, structured=True)
        )
        nodes.extend(sym_nodes)
        edges.extend(sym_edges)

        for cell in row.cells:
            cell_id = cell.id or f"{row_id}:cell::{cell.col}"

            nodes.append(
                (
                    cell_id,
                    {
                        "text": TableRowView(
                            index=row.index, header=row.header, cells=[cell]
                        ).to_text(),
                        "type": "table_cell",
                        "metadata": {
                            **metadata,
                            "index": IndexType.TABLE_CELL,
                            "table_id": table_id,
                            "row_id": row_id,
                            "row_header": row.header,
                            "col_header": cell.col_header,
                            "value": cell.text,
                        },
                    },
                )
            )
            edges.append((row_id, cell_id, {"type": "part_of"}))

            sym_nodes, sym_edges = self._symbol_edges(
                cell_id,
                self._symbols(
                    f"{row.header} {cell.col_header} {cell.text}".strip(),
                    structured=True,
                ),
            )
            nodes.extend(sym_nodes)
            edges.extend(sym_edges)

            key_source = f"{row.header} {cell.col_header}".strip() or row.header
            if not key_source:
                continue

            key_id = f"key::{self.symbol_generator.max_gram(self.tokenizer.tokenize(key_source, False))}"
            val_id = f"val::{self.symbol_generator.max_gram(self.tokenizer.tokenize(cell.text, False))}"

            nodes.append((key_id, {"text": key_source, "type": "key"}))
            nodes.append((val_id, {"text": cell.text, "type": "value"}))

            edges.append((key_id, val_id, {"type": "key_value"}))
            edges.append((cell_id, key_id, {"type": CONTAINS_EDGE}))
            edges.append((cell_id, val_id, {"type": "describes"}))

        return nodes, edges

    # ───────────────────────────────── driver ─────────────────────────────────

    def index_document(
        self,
        document: DocumentModel,
        progress: Optional[ProgressCallback] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> GraphModel:
        """Index all document elements into the graph."""

        start_time = time.time()
        elements = list(document.iter_elements())

        logger.info(f"Starting indexing for {len(elements)} elements")

        all_nodes: Nodes = []
        all_edges: Edges = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:

            futures = []

            # Cancellable unit: cancel queued futures on any exception before
            # executor.shutdown(wait=True), so shutdown doesn't drain the backlog.
            try:
                for i, element in enumerate(elements):

                    if (
                        cancel_check is not None
                        and i % config.CHECKPOINT_BATCH == 0
                        and cancel_check()
                    ):
                        raise JobCancelled(
                            "cancelled while submitting indexing tasks"
                        )

                    if isinstance(element, ParagraphModel):
                        futures.append(
                            executor.submit(self._process_paragraph, document, element)
                        )
                        continue

                    if not isinstance(element, TableModel):
                        continue

                    futures.append(
                        executor.submit(self._process_table, document, element)
                    )

                    metadata = {
                        "heading_path": document._get_heading_path(element),
                        "filename": document.filename,
                        "page": element.page,
                    }

                    context_symbols = self._symbols(
                        " ".join(document.get_table_context(element)),
                        structured=True,
                    )

                    for row in element.normalized_rows():
                        futures.append(
                            executor.submit(
                                self._process_table_row,
                                element.id,
                                row,
                                metadata,
                                context_symbols,
                            )
                        )

                total = len(futures)
                done = 0
                if progress is not None:
                    progress(0, total)

                for future in tqdm(
                    as_completed(futures),
                    total=total,
                    desc="Indexing tasks",
                    unit="task",
                ):
                    result = future.result()
                    done += 1

                    if result:
                        nodes, edges = result
                        all_nodes.extend(nodes)
                        all_edges.extend(edges)

                    if progress is not None:
                        progress(done, total)
            except BaseException:
                for pending in futures:
                    pending.cancel()
                raise

        logger.info(
            f"Collected {len(all_nodes)} nodes and {len(all_edges)} edges"
        )

        insert_start = time.time()

        self.gm.graph.add_nodes_from(all_nodes)
        self.gm.graph.add_edges_from(all_edges)

        logger.info(
            f"Graph population completed in "
            f"{round(time.time() - insert_start, 2)}s"
        )

        with sqlite_conn(GRAPH_DB) as conn:
            self.gm.save(conn)

        logger.info(f"Indexing completed in {round(time.time() - start_time, 2)}s")

        self.gm.clear()
        return self.gm
