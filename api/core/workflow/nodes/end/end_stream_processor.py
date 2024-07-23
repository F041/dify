import logging
from collections.abc import Generator
from typing import cast

from core.workflow.entities.variable_pool import VariablePool
from core.workflow.graph_engine.entities.event import (
    GraphEngineEvent,
    NodeRunStreamChunkEvent,
    NodeRunSucceededEvent,
)
from core.workflow.graph_engine.entities.graph import Graph
from core.workflow.nodes.answer.entities import GenerateRouteChunk, TextGenerateRouteChunk, VarGenerateRouteChunk

logger = logging.getLogger(__name__)


class EndStreamProcessor:

    def __init__(self, graph: Graph, variable_pool: VariablePool) -> None:
        self.graph = graph
        self.variable_pool = variable_pool
        self.stream_param = graph.end_stream_param
        self.end_streamed_variable_selectors: dict[str, list[str]] = {
            end_node_id: [] for end_node_id in graph.end_stream_param.end_stream_variable_selector_mapping
        }

        self.rest_node_ids = graph.node_ids.copy()
        self.current_stream_chunk_generating_node_ids: dict[str, list[str]] = {}

    def process(self,
                generator: Generator[GraphEngineEvent, None, None]
                ) -> Generator[GraphEngineEvent, None, None]:
        for event in generator:
            if isinstance(event, NodeRunStreamChunkEvent):
                if event.route_node_state.node_id in self.current_stream_chunk_generating_node_ids:
                    stream_out_answer_node_ids = self.current_stream_chunk_generating_node_ids[
                        event.route_node_state.node_id
                    ]
                else:
                    stream_out_answer_node_ids = self._get_stream_out_answer_node_ids(event)
                    self.current_stream_chunk_generating_node_ids[
                        event.route_node_state.node_id
                    ] = stream_out_answer_node_ids

                for _ in stream_out_answer_node_ids:
                    yield event
            elif isinstance(event, NodeRunSucceededEvent):
                yield event
                if event.route_node_state.node_id in self.current_stream_chunk_generating_node_ids:
                    # update self.route_position after all stream event finished
                    for answer_node_id in self.current_stream_chunk_generating_node_ids[event.route_node_state.node_id]:
                        self.route_position[answer_node_id] += 1

                    del self.current_stream_chunk_generating_node_ids[event.route_node_state.node_id]

                # remove unreachable nodes
                self._remove_unreachable_nodes(event)

                # generate stream outputs
                yield from self._generate_stream_outputs_when_node_finished(event)
            else:
                yield event

    def reset(self) -> None:
        self.route_position = {}
        for answer_node_id, route_chunks in self.generate_routes.answer_generate_route.items():
            self.route_position[answer_node_id] = 0
        self.rest_node_ids = self.graph.node_ids.copy()

    def _remove_unreachable_nodes(self, event: NodeRunSucceededEvent) -> None:
        finished_node_id = event.route_node_state.node_id

        if finished_node_id not in self.rest_node_ids:
            return

        # remove finished node id
        self.rest_node_ids.remove(finished_node_id)

        run_result = event.route_node_state.node_run_result
        if not run_result:
            return

        if run_result.edge_source_handle:
            reachable_node_ids = []
            unreachable_first_node_ids = []
            for edge in self.graph.edge_mapping[finished_node_id]:
                if (edge.run_condition
                        and edge.run_condition.branch_identify
                        and run_result.edge_source_handle == edge.run_condition.branch_identify):
                    reachable_node_ids.extend(self._fetch_node_ids_in_reachable_branch(edge.target_node_id))
                    continue
                else:
                    unreachable_first_node_ids.append(edge.target_node_id)

            for node_id in unreachable_first_node_ids:
                self._remove_node_ids_in_unreachable_branch(node_id, reachable_node_ids)

    def _fetch_node_ids_in_reachable_branch(self, node_id: str) -> list[str]:
        node_ids = []
        for edge in self.graph.edge_mapping.get(node_id, []):
            node_ids.append(edge.target_node_id)
            node_ids.extend(self._fetch_node_ids_in_reachable_branch(edge.target_node_id))
        return node_ids

    def _remove_node_ids_in_unreachable_branch(self, node_id: str, reachable_node_ids: list[str]) -> None:
        """
        remove target node ids until merge
        """
        self.rest_node_ids.remove(node_id)
        for edge in self.graph.edge_mapping.get(node_id, []):
            if edge.target_node_id in reachable_node_ids:
                continue

            self._remove_node_ids_in_unreachable_branch(edge.target_node_id, reachable_node_ids)

    def _generate_stream_outputs_when_node_finished(self,
                                                    event: NodeRunSucceededEvent
                                                    ) -> Generator[GraphEngineEvent, None, None]:
        """
        Generate stream outputs.
        :param event: node run succeeded event
        :return:
        """
        for answer_node_id, position in self.route_position.items():
            # all depends on answer node id not in rest node ids
            if (event.route_node_state.node_id != answer_node_id
                    and (answer_node_id not in self.rest_node_ids
                    or not all(dep_id not in self.rest_node_ids
                       for dep_id in self.generate_routes.answer_dependencies[answer_node_id]))):
                continue

            route_position = self.route_position[answer_node_id]
            route_chunks = self.generate_routes.answer_generate_route[answer_node_id][route_position:]

            for route_chunk in route_chunks:
                if route_chunk.type == GenerateRouteChunk.ChunkType.TEXT:
                    route_chunk = cast(TextGenerateRouteChunk, route_chunk)
                    yield NodeRunStreamChunkEvent(
                        chunk_content=route_chunk.text,
                        route_node_state=event.route_node_state,
                        parallel_id=event.parallel_id,
                    )
                else:
                    route_chunk = cast(VarGenerateRouteChunk, route_chunk)
                    value_selector = route_chunk.value_selector
                    if not value_selector:
                        break

                    value = self.variable_pool.get(
                        value_selector
                    )

                    if value is None:
                        break

                    text = value.markdown

                    if text:
                        yield NodeRunStreamChunkEvent(
                            chunk_content=text,
                            from_variable_selector=value_selector,
                            route_node_state=event.route_node_state,
                            parallel_id=event.parallel_id,
                        )

                self.route_position[answer_node_id] += 1

    def _get_stream_out_answer_node_ids(self, event: NodeRunStreamChunkEvent) -> list[str]:
        """
        Is stream out support
        :param event: queue text chunk event
        :return:
        """
        if not event.from_variable_selector:
            return []

        stream_output_value_selector = event.from_variable_selector
        if not stream_output_value_selector:
            return []

        stream_out_answer_node_ids = []
        for answer_node_id, position in self.route_position.items():
            if answer_node_id not in self.rest_node_ids:
                continue

            # all depends on answer node id not in rest node ids
            if all(dep_id not in self.rest_node_ids
                   for dep_id in self.generate_routes.answer_dependencies[answer_node_id]):
                route_position = self.route_position[answer_node_id]
                if route_position >= len(self.generate_routes.answer_generate_route[answer_node_id]):
                    continue

                route_chunk = self.generate_routes.answer_generate_route[answer_node_id][route_position]

                if route_chunk.type != GenerateRouteChunk.ChunkType.VAR:
                    continue

                route_chunk = cast(VarGenerateRouteChunk, route_chunk)
                value_selector = route_chunk.value_selector

                # check chunk node id is before current node id or equal to current node id
                if value_selector != stream_output_value_selector:
                    continue

                stream_out_answer_node_ids.append(answer_node_id)

        return stream_out_answer_node_ids
