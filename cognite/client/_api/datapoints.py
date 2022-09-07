from __future__ import annotations

import functools
import heapq
import math
import statistics
from abc import ABC, abstractmethod
from concurrent.futures import CancelledError, as_completed
from copy import copy
from datetime import datetime
from itertools import chain
from random import random
from time import monotonic_ns
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)

from cognite.client._api.datapoint_constants import (
    DPS_LIMIT,
    DPS_LIMIT_AGG,
    FETCH_TS_LIMIT,
    POST_DPS_OBJECTS_LIMIT,
    RETRIEVE_LATEST_LIMIT,
    CustomDatapoints,
    DatapointsExternalIdTypes,
    DatapointsFromAPI,
    DatapointsIdTypes,
    DatapointsPayload,
)
from cognite.client._api.datapoint_tasks import (
    BaseConcurrentTask,
    SplittingFetchSubtask,
    _SingleTSQueryBase,
    _SingleTSQueryValidator,
)
from cognite.client._api.synthetic_time_series import SyntheticDatapointsAPI
from cognite.client._api_client import APIClient
from cognite.client.data_classes import (
    Datapoints,
    DatapointsArray,
    DatapointsArrayList,
    DatapointsList,
    DatapointsQuery,
)
from cognite.client.exceptions import CogniteAPIError, CogniteNotFoundError
from cognite.client.utils._auxiliary import assert_type, local_import, split_into_chunks, split_into_n_parts
from cognite.client.utils._concurrency import execute_tasks_concurrently
from cognite.client.utils._identifier import Identifier, IdentifierSequence
from cognite.client.utils._priority_tpe import PriorityThreadPoolExecutor  # type: ignore
from cognite.client.utils._time import timestamp_to_ms

if TYPE_CHECKING:
    from concurrent.futures import Future

    import pandas as pd


TSQueryList = List[_SingleTSQueryBase]
PoolSubtaskType = Tuple[int, float, int, SplittingFetchSubtask]


def dps_fetch_selector(dps_client: DatapointsAPI, user_queries: Sequence[DatapointsQuery]) -> DpsFetchStrategy:
    max_workers = dps_client._config.max_workers
    if max_workers < 1:
        raise RuntimeError(f"Invalid option for `max_workers={max_workers}`. Must be at least 1")
    all_queries, agg_queries, raw_queries = validate_and_split_user_queries(user_queries)

    # Running mode is decided based on how many time series are requested VS. number of workers:
    if len(all_queries) <= max_workers:
        # Start shooting requests from the hip immediately:
        return EagerDpsFetcher(dps_client, all_queries, agg_queries, raw_queries, max_workers)
    # Fetch a smaller, chunked batch from all time series - then chunk away:
    return ChunkingDpsFetcher(dps_client, all_queries, agg_queries, raw_queries, max_workers)


def validate_and_split_user_queries(
    user_queries: Sequence[DatapointsQuery],
) -> Tuple[TSQueryList, TSQueryList, TSQueryList]:
    split_qs: Tuple[TSQueryList, TSQueryList] = [], []
    all_queries = list(
        chain.from_iterable(
            query.validate_and_create_single_queries() for query in map(_SingleTSQueryValidator, user_queries)
        )
    )
    for query in all_queries:
        split_qs[query.is_raw_query].append(query)
    return (all_queries, *split_qs)


class DpsFetchStrategy(ABC):
    def __init__(
        self,
        dps_client: DatapointsAPI,
        all_queries: TSQueryList,
        agg_queries: TSQueryList,
        raw_queries: TSQueryList,
        max_workers: int,
    ) -> None:
        self.dps_client = dps_client
        self.all_queries = all_queries
        self.agg_queries = agg_queries
        self.raw_queries = raw_queries
        self.max_workers = max_workers
        self.n_queries = len(all_queries)

    def fetch_all_datapoints(self) -> DatapointsArrayList:
        with PriorityThreadPoolExecutor(max_workers=self.max_workers) as pool:
            ordered_results = self.fetch_all(pool)
        return self._finalize_tasks(ordered_results)

    def _finalize_tasks(self, ordered_results: List[BaseConcurrentTask]) -> DatapointsArrayList:
        return DatapointsArrayList(
            [ts_task.get_result() for ts_task in ordered_results],
            cognite_client=self.dps_client._cognite_client,
        )

    @abstractmethod
    def fetch_all(self, pool: PriorityThreadPoolExecutor) -> List[BaseConcurrentTask]:
        ...

    @abstractmethod
    def _create_initial_tasks(self, pool: PriorityThreadPoolExecutor) -> Tuple[Dict, Dict]:
        ...


class EagerDpsFetcher(DpsFetchStrategy):
    def request_datapoints_jit(
        self,
        task: SplittingFetchSubtask,
        payload: Optional[CustomDatapoints] = None,
    ) -> List[Optional[DatapointsFromAPI]]:
        # Note: We delay getting the next payload as much as possible; this way, when we count number of
        # points left to fetch JIT, we have the most up-to-date estimate (and may quit early):
        if (item := task.get_next_payload()) is None:
            return [None]

        (payload := copy(payload) or {})["items"] = [item]  # type: ignore [typeddict-item]
        return self.dps_client._post(
            self.dps_client._RESOURCE_PATH + "/list", json=cast(Dict[str, Any], payload)
        ).json()["items"]

    def fetch_all(self, pool: PriorityThreadPoolExecutor) -> List[BaseConcurrentTask]:
        futures_dct, ts_task_lookup = self._create_initial_tasks(pool)

        # Run until all top level tasks are complete:
        while futures_dct:
            future = next(as_completed(futures_dct))
            ts_task = (subtask := futures_dct.pop(future)).parent
            res = self._get_result_with_exception_handling(future, ts_task, ts_task_lookup, futures_dct)
            if res is None:
                continue
            # We may dynamically split subtasks based on what % of time range was returned:
            if new_subtasks := subtask.store_partial_result(res):
                self._queue_new_subtasks(pool, futures_dct, new_subtasks)
            if ts_task.is_done:  # "Parent" ts task might be done before a subtask is finished
                if all(parent.is_done for parent in ts_task_lookup.values()):
                    pool.shutdown(wait=False)
                    break
                if ts_task.has_limit:
                    # For finished limited queries, cancel all unstarted futures for same parent:
                    self._cancel_futures_for_finished_ts_task(ts_task, futures_dct)
                continue
            elif subtask.is_done:
                continue
            self._queue_new_subtasks(pool, futures_dct, [subtask])
        # Return only non-missing time series tasks in correct order given by `all_queries`:
        return list(filter(None, map(ts_task_lookup.get, self.all_queries)))

    def _create_initial_tasks(
        self, pool: PriorityThreadPoolExecutor
    ) -> Tuple[Dict[Future, SplittingFetchSubtask], Dict[_SingleTSQueryBase, BaseConcurrentTask]]:
        futures_dct: Dict[Future, SplittingFetchSubtask] = {}
        ts_task_lookup, payload = {}, {"ignoreUnknownIds": False}
        for query in self.all_queries:
            ts_task = ts_task_lookup[query] = query.ts_task_type(query, eager_mode=True)
            for subtask in ts_task.split_into_subtasks(self.max_workers, self.n_queries):
                future = pool.submit(self.request_datapoints_jit, subtask, payload, priority=subtask.priority)
                futures_dct[future] = subtask
        return futures_dct, ts_task_lookup

    def _queue_new_subtasks(
        self,
        pool: PriorityThreadPoolExecutor,
        futures_dct: Dict[Future, SplittingFetchSubtask],
        new_subtasks: List[SplittingFetchSubtask],
    ) -> None:
        for task in new_subtasks:
            future = pool.submit(self.request_datapoints_jit, task, priority=task.priority)
            futures_dct[future] = task

    def _get_result_with_exception_handling(
        self,
        future: Future,
        ts_task: BaseConcurrentTask,
        ts_task_lookup: Dict[_SingleTSQueryBase, BaseConcurrentTask],
        futures_dct: Dict[Future, SplittingFetchSubtask],
    ) -> Optional[DatapointsFromAPI]:
        try:
            return future.result()[0]
        except CancelledError:
            return None
        except CogniteAPIError as e:
            if not (e.code == 400 and e.missing and ts_task.query.ignore_unknown_ids):
                raise
            elif ts_task.is_done:
                return None
            ts_task.is_done = True
            del ts_task_lookup[ts_task.query]
            self._cancel_futures_for_finished_ts_task(ts_task, futures_dct)
            return None

    def _cancel_futures_for_finished_ts_task(
        self, ts_task: BaseConcurrentTask, futures_dct: Dict[Future, SplittingFetchSubtask]
    ) -> None:
        for future, subtask in futures_dct.copy().items():
            # TODO: Change to loop over parent.subtasks?
            if subtask.parent is ts_task:
                future.cancel()
                del futures_dct[future]


class ChunkingDpsFetcher(DpsFetchStrategy):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        # To chunk efficiently, we have subtask pools (heap queues) that we use to prioritise subtasks
        # when building/combining subtasks into a full query:
        self.raw_subtask_pool: List[PoolSubtaskType] = []
        self.agg_subtask_pool: List[PoolSubtaskType] = []
        self.subtask_pools = (self.agg_subtask_pool, self.raw_subtask_pool)
        # Combined partial queries storage (chunked, but not enough to fill a request):
        self.next_items: List[CustomDatapoints] = []
        self.next_subtasks: List[SplittingFetchSubtask] = []

    def fetch_all(self, pool: PriorityThreadPoolExecutor) -> List[BaseConcurrentTask]:
        # The initial tasks are important - as they tell us which time series are missing,
        # which are string etc. We use this info when we choose the best fetch-strategy.
        ts_task_lookup, missing_to_raise = {}, set()
        initial_query_limits, initial_futures_dct = self._create_initial_tasks(pool)

        for future in as_completed(initial_futures_dct):
            res = future.result()
            chunk_agg_qs, chunk_raw_qs = initial_futures_dct.pop(future)
            new_ts_tasks, chunk_missing = self._create_ts_tasks_and_handle_missing(
                res, chunk_agg_qs, chunk_raw_qs, initial_query_limits
            )
            missing_to_raise.update(chunk_missing)
            ts_task_lookup.update(new_ts_tasks)

        if missing_to_raise:
            raise CogniteNotFoundError(not_found=[q.identifier.as_dict(camel_case=False) for q in missing_to_raise])

        if ts_tasks_left := self._update_queries_with_new_chunking_limit(ts_task_lookup):
            self._add_to_subtask_pools(
                chain.from_iterable(
                    task.split_into_subtasks(max_workers=self.max_workers, n_tot_queries=len(ts_tasks_left))
                    for task in ts_tasks_left
                )
            )
            futures_dct: Dict[Future, List[SplittingFetchSubtask]] = {}
            self._queue_new_subtasks(pool, futures_dct)
            self._fetch_until_complete(pool, futures_dct, ts_task_lookup)
        # Return only non-missing time series tasks in correct order given by `all_queries`:
        return list(filter(None, map(ts_task_lookup.get, self.all_queries)))

    def _fetch_until_complete(
        self,
        pool: PriorityThreadPoolExecutor,
        futures_dct: Dict[Future, List[SplittingFetchSubtask]],
        ts_task_lookup: Dict[_SingleTSQueryBase, BaseConcurrentTask],
    ) -> None:
        while futures_dct:
            future = next(as_completed(futures_dct))
            res_lst, subtask_lst = future.result(), futures_dct.pop(future)
            for subtask, res in zip(subtask_lst, res_lst):
                # We may dynamically split subtasks based on what % of time range was returned:
                if new_subtasks := subtask.store_partial_result(res):
                    self._add_to_subtask_pools(new_subtasks)
                if not subtask.is_done:
                    self._add_to_subtask_pools([subtask])
            # Check each parent in current batch once if we may cancel some queued subtasks:
            if done_ts_tasks := {sub.parent for sub in subtask_lst if sub.parent.is_done}:
                self._cancel_subtasks(done_ts_tasks)

            self._queue_new_subtasks(pool, futures_dct)

            if all(task.is_done for task in ts_task_lookup.values()):
                pool.shutdown(wait=False)
                return None

    def request_datapoints(self, payload: DatapointsPayload) -> List[Optional[DatapointsFromAPI]]:
        return self.dps_client._post(
            self.dps_client._RESOURCE_PATH + "/list", json=cast(Dict[str, Any], payload)
        ).json()["items"]

    def _create_initial_tasks(
        self, pool: PriorityThreadPoolExecutor
    ) -> Tuple[Dict[_SingleTSQueryBase, int], Dict[Future, Tuple[TSQueryList, TSQueryList]]]:
        initial_query_limits: Dict[_SingleTSQueryBase, int] = {}
        initial_futures_dct: Dict[Future, Tuple[TSQueryList, TSQueryList]] = {}
        # Optimal queries uses the entire worker pool. We may be forced to use more (queue) when we
        # can't fit all individual time series (maxes out at `FETCH_TS_LIMIT * max_workers`):
        n_queries = max(self.max_workers, math.ceil(self.n_queries / FETCH_TS_LIMIT))
        splitter = functools.partial(split_into_n_parts, n=n_queries)
        for query_chunks in zip(splitter(self.agg_queries), splitter(self.raw_queries)):
            # Agg and raw limits are independent in the query, so we max out on both:
            items = []
            for queries, max_lim in zip(query_chunks, [DPS_LIMIT_AGG, DPS_LIMIT]):
                maxed_limits = self._find_initial_query_limits(
                    [q.capped_limit for q in queries], max_lim  # type: ignore [attr-defined]
                )
                initial_query_limits.update(
                    chunk_query_limits := dict(zip(queries, maxed_limits))  # type: ignore [call-overload]
                )
                items.extend([{**q.to_payload(), "limit": lim} for q, lim in chunk_query_limits.items()])

            payload = {"ignoreUnknownIds": True, "items": items}
            future = pool.submit(self.request_datapoints, payload, priority=0)
            initial_futures_dct[future] = query_chunks  # type: ignore [assignment]
        return initial_query_limits, initial_futures_dct

    def _create_ts_tasks_and_handle_missing(
        self,
        results: List[DatapointsFromAPI],
        chunk_agg_qs: TSQueryList,
        chunk_raw_qs: TSQueryList,
        initial_query_limits: Dict[_SingleTSQueryBase, int],
    ) -> Tuple[Dict[_SingleTSQueryBase, BaseConcurrentTask], Set[_SingleTSQueryBase]]:
        if len(results) == len(chunk_agg_qs) + len(chunk_raw_qs):
            to_raise: Set[_SingleTSQueryBase] = set()
        else:
            # We have at least 1 missing time series:
            chunk_agg_qs, chunk_raw_qs, to_raise = self._handle_missing_ts(results, chunk_agg_qs, chunk_raw_qs)
        self._update_queries_is_string(results, chunk_raw_qs)
        # Align initial results with corresponding queries and create tasks:
        ts_tasks = {
            query: query.ts_task_type(
                query, eager_mode=False, first_dps_batch=res, first_limit=initial_query_limits[query]
            )
            for res, query in zip(results, chain(chunk_agg_qs, chunk_raw_qs))
        }
        return ts_tasks, to_raise

    def _add_to_subtask_pools(self, new_subtasks: Iterable[SplittingFetchSubtask]) -> None:
        for task in new_subtasks:
            # We leverage how tuples are compared to prioritise items. First `priority`, then `payload limit`
            # (to easily group smaller queries), then an element to always break ties, but keep order (never use tasks themselves):
            limit = min(task.n_dps_left, task.max_query_limit)
            new_subtask: PoolSubtaskType = (task.priority, limit, monotonic_ns() + random(), task)
            heapq.heappush(self.subtask_pools[task.is_raw_query], new_subtask)

    def _queue_new_subtasks(
        self, pool: PriorityThreadPoolExecutor, futures_dct: Dict[Future, List[SplittingFetchSubtask]]
    ) -> None:
        qsize = pool._work_queue.qsize()  # Approximate size of the queue (number of unstarted tasks)
        if qsize > 2 * self.max_workers:
            # Each worker has more than 2 tasks already awaiting in the thread pool queue already, so we
            # hold off on combining new subtasks just yet (allows better prioritisation as more new tasks arrive).
            return None
        # When pool queue has few awaiting tasks, we empty the subtasks pool into a partial request:
        return_partial_payload = qsize <= min(5, math.ceil(self.max_workers / 2))
        combined_requests = self._combine_subtasks_into_requests(return_partial_payload)

        for payload, subtask_lst, priority in combined_requests:
            future = pool.submit(self.request_datapoints, payload, priority=priority)
            futures_dct[future] = subtask_lst

    def _combine_subtasks_into_requests(
        self,
        return_partial_payload: bool,
    ) -> Iterator[Tuple[DatapointsPayload, List[SplittingFetchSubtask], float]]:

        while any(self.subtask_pools):  # As long as both are not empty
            payload_at_max_items, payload_is_full = False, [False, False]
            for task_pool, request_max_limit, is_raw in zip(
                self.subtask_pools, (DPS_LIMIT_AGG, DPS_LIMIT), [False, True]
            ):
                if not task_pool:
                    continue
                limit_used = 0
                if self.next_items:  # Happens when we continue building on a previous "partial payload"
                    limit_used = sum(  # Tally up either raw or agg query `limit_used`
                        item["limit"]
                        for item, task in zip(self.next_items, self.next_subtasks)
                        if task.is_raw_query is is_raw
                    )
                while task_pool:
                    if len(self.next_items) + 1 > FETCH_TS_LIMIT:
                        payload_at_max_items = True
                        break
                    # Highest priority task is always at index 0 (heap magic):
                    *_, next_task = task_pool[0]
                    next_payload = next_task.get_next_payload()
                    if next_payload is None or next_task.is_done:
                        # Parent task finished before subtask and has been marked done already:
                        heapq.heappop(task_pool)  # Pop to remove from heap
                        continue
                    next_limit = next_payload["limit"]
                    if limit_used + next_limit <= request_max_limit:
                        self.next_items.append(next_payload)
                        self.next_subtasks.append(next_task)
                        limit_used += next_limit
                        heapq.heappop(task_pool)
                    else:
                        payload_is_full[is_raw] = True  # type: ignore [has-type]
                        break

                payload_done = (
                    payload_at_max_items
                    or all(payload_is_full)
                    or (payload_is_full[0] and not self.raw_subtask_pool)
                    or (payload_is_full[1] and not self.agg_subtask_pool)
                    or (return_partial_payload and not any(self.subtask_pools))
                )
                if payload_done:
                    priority = statistics.mean(task.priority for task in self.next_subtasks)
                    payload: DatapointsPayload = {"items": self.next_items[:]}
                    yield payload, self.next_subtasks[:], cast(float, priority)

                    self.next_items, self.next_subtasks = [], []
                    break

    def _update_queries_with_new_chunking_limit(
        self, ts_task_lookup: Dict[_SingleTSQueryBase, BaseConcurrentTask]
    ) -> List[BaseConcurrentTask]:
        queries = [query for query, task in ts_task_lookup.items() if not task.is_done]
        tot_raw = sum(q.is_raw_query for q in queries)
        tot_agg = len(queries) - tot_raw
        n_raw_chunk = min(FETCH_TS_LIMIT, math.ceil((tot_raw or 1) / 10))
        n_agg_chunk = min(FETCH_TS_LIMIT, math.ceil((tot_agg or 1) / 10))
        max_limit_raw = math.floor(DPS_LIMIT / n_raw_chunk)
        max_limit_agg = math.floor(DPS_LIMIT_AGG / n_agg_chunk)
        for query in queries:
            if query.is_raw_query:
                query.override_max_query_limit(max_limit_raw)
            else:
                query.override_max_query_limit(max_limit_agg)
        return [ts_task_lookup[query] for query in queries]

    def _cancel_subtasks(self, done_ts_tasks: Set[BaseConcurrentTask]) -> None:
        for ts_task in done_ts_tasks:
            # We do -not- want to iterate/mutate the heapqs, so we mark subtasks as done instead:
            for subtask in ts_task.subtasks:
                subtask.is_done = True

    @staticmethod
    def _find_initial_query_limits(limits: List[int], max_limit: int) -> List[int]:
        actual_lims = [0] * len(limits)
        not_done = set(range(len(limits)))
        while not_done:
            part = max_limit // len(not_done)
            if not part:
                # We still might not have not reached max_limit, but we can no longer distribute evenly
                break
            rm_idx = set()
            for i in not_done:
                i_part = min(part, limits[i])  # A query of limit=10 does not need more of max_limit than 10
                actual_lims[i] += i_part
                max_limit -= i_part
                if i_part == limits[i]:
                    rm_idx.add(i)
                else:
                    limits[i] -= i_part
            not_done -= rm_idx
        return actual_lims

    @staticmethod
    def _update_queries_is_string(res: List[DatapointsFromAPI], queries: TSQueryList) -> None:
        is_string = {("id", r["id"]) for r in res if r["isString"]}.union(
            ("externalId", r["externalId"]) for r in res if r["isString"]
        )
        for q in queries:
            q.is_string = q.identifier.as_tuple() in is_string

    @staticmethod
    def _handle_missing_ts(
        res: List[DatapointsFromAPI],
        agg_queries: TSQueryList,
        raw_queries: TSQueryList,
    ) -> Tuple[TSQueryList, TSQueryList, Set[_SingleTSQueryBase]]:
        missing, to_raise = set(), set()
        not_missing = {("id", r["id"]) for r in res}.union(("externalId", r["externalId"]) for r in res)
        for query in chain(agg_queries, raw_queries):
            # Update _SingleTSQueryBase objects with `is_missing` status:
            query.is_missing = query.identifier.as_tuple() not in not_missing
            if query.is_missing:
                missing.add(query)
                # We might be handling multiple simultaneous top-level queries, each with a
                # different settings for "ignore unknown":
                if not query.ignore_unknown_ids:
                    to_raise.add(query)
        agg_queries = [q for q in agg_queries if not q.is_missing]
        raw_queries = [q for q in raw_queries if not q.is_missing]
        return agg_queries, raw_queries, to_raise


class DatapointsAPI(APIClient):
    _RESOURCE_PATH = "/timeseries/data"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.synthetic = SyntheticDatapointsAPI(
            self._config, api_version=self._api_version, cognite_client=self._cognite_client
        )

    def retrieve(
        self,
        start: Union[int, str, datetime, None] = None,
        end: Union[int, str, datetime, None] = None,
        id: Optional[DatapointsIdTypes] = None,
        external_id: Optional[DatapointsExternalIdTypes] = None,
        aggregates: Optional[List[str]] = None,
        granularity: Optional[str] = None,
        limit: Optional[int] = None,
        include_outside_points: bool = False,
        ignore_unknown_ids: bool = False,
    ) -> Union[None, DatapointsArray, DatapointsArrayList]:
        """`Retrieve datapoints for one or more time series. <https://docs.cognite.com/api/v1/#operation/getMultiTimeSeriesDatapoints>`_

        **Note**: All arguments are optional, as long as at least one identifier is given. When passing aggregates, granularity must also be given.
        When passing dict objects with specific parameters, these will take precedence. See examples below.

        Args:
            start (Union[int, str, datetime]): Inclusive start. Default: 1970-01-01 UTC.
            end (Union[int, str, datetime]): Exclusive end. Default: "now"
            id (DatapointsIdTypes): Id, dict (with id) or (mixed) list of these. See examples below.
            external_id (DatapointsExternalIdTypes): External id, dict (with external id) or (mixed) list of these. See examples below.
            aggregates (List[str]): List of aggregate functions to apply. Default: No aggregates (raw datapoints)
            granularity (str): The granularity to fetch aggregates at. e.g. '1s', '2h', '10d'. Default: None.
            limit (int): Maximum number of datapoints to return for each time series. Default: None (no limit)
            include_outside_points (bool): Whether or not to include outside points. Not allowed when fetching aggregates. Default: False
            ignore_unknown_ids (bool): Whether or not to ignore missing time series rather than raising an exception. Default: False

        Returns:
            Union[None, DatapointsArray, DatapointsArrayList]: A `DatapointsArray` containing the requested data, or a `DatapointsArrayList` if multiple time series was asked for. If `ignore_unknown_ids` is `True`, a single time series is requested and it is not found, the function will return `None`.

        Examples:

            You can specify the identifiers of the datapoints you wish to retrieve in a number of ways. In this example
            we are using the time-ago format to get raw data for the time series with id=42 from 2 weeks ago up until now::

                >>> from cognite.client import CogniteClient
                >>> client = CogniteClient()
                >>> dps = client.time_series.data.retrieve(id=42, start="2w-ago")

            You can also get aggregated values, such as the average. Here we are getting daily averages for all of 2018 for
            two different time series. Note that we are fetching them using their external ids::

                >>> from datetime import datetime, timezone
                >>> dps = client.time_series.data.retrieve(
                ...    external_id=["foo", "bar"],
                ...    start=datetime(2018, 1, 1, tzinfo=timezone.utc),
                ...    end=datetime(2018, 1, 1, tzinfo=timezone.utc),
                ...    aggregates=["average"],
                ...    granularity="1d")

            Note that all parameters can be individually set if you pass (one or more) dictionaries. If you also pass top-level
            parameters, these will be overwritten by the individual parameters (when both exist). You are free to mix ids and external ids.

            Let's say you want different aggregates and end-times for few time series:

                >>> dps = client.time_series.data.retrieve(
                ...     id=[
                ...         {"id": 42, "end": "2d-ago", "aggregates": ["average"]},
                ...         {"id": 11, "end": "1d-ago", "aggregates": ["min", "max", "count"]},
                ...     ],
                ...     external_id={"external_id": "foo", "aggregates": ["max"]},
                ...     start="5d-ago",
                ...     granularity="1h")

            All parameters except `ignore_unknown_ids` can be individually set (if you want to specify multiple values
            for this, you'll have to use the `.query` endpoint).

                >>> start_time = "2w-ago"
                >>> limit = None
                >>> ts1 = 1337
                >>> ts2 = {
                ...     "id": 42,
                ...     "start": -12345,  # Overrides `start_time`
                ...     "end": "1h-ago",
                ...     "limit": 1000,  # Overrides `limit`
                ...     "include_outside_points": True
                ... }
                >>> ts3 = {
                ...     "id": 11,
                ...     "end": "1h-ago",
                ...     "aggregates": ["max"],
                ...     "granularity": "42h",
                ...     "include_outside_points": False
                ... }
                >>> dps = client.time_series.data.retrieve(
                ...    id=[ts1, ts2, ts3], start=start_time, limit=limit
                ... )
        """
        query = DatapointsQuery(
            start=start,
            end=end,
            id=id,
            external_id=external_id,
            aggregates=aggregates,
            granularity=granularity,
            limit=limit,
            include_outside_points=include_outside_points,
            ignore_unknown_ids=ignore_unknown_ids,
        )
        fetcher = dps_fetch_selector(self, user_queries=[query])
        dps_list = fetcher.fetch_all_datapoints()
        if not query.is_single_identifier:
            return dps_list
        elif not dps_list and ignore_unknown_ids:
            return None
        return dps_list[0]

    def retrieve_latest(
        self,
        id: Union[int, List[int]] = None,
        external_id: Union[str, List[str]] = None,
        before: Union[int, str, datetime] = None,
        ignore_unknown_ids: bool = False,
    ) -> Union[Datapoints, DatapointsList]:
        """`Get the latest datapoint for one or more time series <https://docs.cognite.com/api/v1/#operation/getLatest>`_

        Args:
            id (Union[int, List[int]]): Id or list of ids.
            external_id (Union[str, List[str]): External id or list of external ids.
            before: (Union[int, str, datetime]): Get latest datapoint before this time.
            ignore_unknown_ids (bool): Ignore IDs and external IDs that are not found rather than throw an exception.

        Returns:
            Union[Datapoints, DatapointsList]: A Datapoints object containing the requested data, or a list of such objects.

        Examples:

            Getting the latest datapoint in a time series. This method returns a Datapoints object, so the datapoint will
            be the first element::

                >>> from cognite.client import CogniteClient
                >>> c = CogniteClient()
                >>> res = c.time_series.data.retrieve_latest(id=1)[0]

            You can also get the first datapoint before a specific time::

                >>> from cognite.client import CogniteClient
                >>> c = CogniteClient()
                >>> res = c.time_series.data.retrieve_latest(id=1, before="2d-ago")[0]

            If you need the latest datapoint for multiple time series simply give a list of ids. Note that we are
            using external ids here, but either will work::

                >>> from cognite.client import CogniteClient
                >>> c = CogniteClient()
                >>> res = c.time_series.data.retrieve_latest(external_id=["abc", "def"])
                >>> latest_abc = res[0][0]
                >>> latest_def = res[1][0]
        """
        before = timestamp_to_ms(before) if before else None
        id_seq = IdentifierSequence.load(id, external_id)
        all_ids = id_seq.as_dicts()
        if before:
            for id_ in all_ids:
                id_["before"] = before

        tasks = [
            {
                "url_path": self._RESOURCE_PATH + "/latest",
                "json": {"items": chunk, "ignoreUnknownIds": ignore_unknown_ids},
            }
            for chunk in split_into_chunks(all_ids, RETRIEVE_LATEST_LIMIT)
        ]
        tasks_summary = execute_tasks_concurrently(self._post, tasks, max_workers=self._config.max_workers)
        if tasks_summary.exceptions:
            raise tasks_summary.exceptions[0]
        res = tasks_summary.joined_results(lambda res: res.json()["items"])
        if id_seq.is_singleton():
            return Datapoints._load(res[0], cognite_client=self._cognite_client)
        return DatapointsList._load(res, cognite_client=self._cognite_client)

    def query(
        self,
        query: Union[Sequence[DatapointsQuery], DatapointsQuery],
    ) -> DatapointsArrayList:
        """Get datapoints for one or more time series by passing query objects directly.

        **Note**: Before version 5.0.0, this method was the only way to retrieve datapoints easily with individual fetch settings.
        This is no longer the case: `query` only differs from `retrieve` in that you can specify different values for `ignore_unknown_ids` for the multiple
        query objects you pass, which is quite a niche feature. Since this is a boolean parameter, the only real use case is to pass exactly
        two queries to this method; the "can be" missing and the "can't be" missing groups. If you do not need this functionality,
        stick with the `retrieve` endpoint.

        Args:
            query (Union[DatapointsQuery, Sequence[DatapointsQuery]): The datapoint queries

        Returns:
            DatapointsArrayList: The requested datapoints. Note that you always get a single `DatapointsArrayList` returned. The order is the ids of the first query, then the external ids of the first query, then similarly for the next queries.

        Examples:

            This method is useful if one group of one or more time series can be missing AND another, can't be missing::

                >>> from cognite.client import CogniteClient
                >>> from cognite.client.data_classes import DatapointsQuery
                >>> c = CogniteClient()
                >>> query1 = DatapointsQuery(id=[111, 222], start="2d-ago", end="now", ignore_unknown_ids=False)
                >>> query2 = DatapointsQuery(external_id="foo", start=2000, end="now", ignore_unknown_ids=True)
                >>> res = c.time_series.data.query([query1, query2])
        """
        if isinstance(query, DatapointsQuery):
            query = [query]
        fetcher = dps_fetch_selector(self, user_queries=query)
        return fetcher.fetch_all_datapoints()

    def insert(
        self,
        datapoints: Union[
            Datapoints,
            DatapointsArray,
            List[Dict[Union[int, float, datetime], Union[int, float, str]]],
            List[Tuple[Union[int, float, datetime], Union[int, float, str]]],
        ],
        id: int = None,
        external_id: str = None,
    ) -> None:
        """Insert datapoints into a time series

        Timestamps can be represented as milliseconds since epoch or datetime objects.

        Args:
            datapoints(Union[List[Dict], List[Tuple],Datapoints]): The datapoints you wish to insert. Can either be a list of tuples,
                a list of dictionaries, a Datapoints object or a DatapointsArray object. See examples below.
            id (int): Id of time series to insert datapoints into.
            external_id (str): External id of time series to insert datapoint into.

        Returns:
            None

        Examples:

            Your datapoints can be a list of tuples where the first element is the timestamp and the second element is
            the value::


                >>> from cognite.client import CogniteClient
                >>> from datetime import datetime, timezone
                >>> c = CogniteClient()
                >>> # With datetime objects:
                >>> datapoints = [
                ...     (datetime(2018,1,1, tzinfo=timezone.utc), 1000),
                ...     (datetime(2018,1,2, tzinfo=timezone.utc), 2000),
                ... ]
                >>> c.time_series.data.insert(datapoints, id=1)
                >>> # With ms since epoch:
                >>> datapoints = [(150000000000, 1000), (160000000000, 2000)]
                >>> c.time_series.data.insert(datapoints, id=2)

            Or they can be a list of dictionaries::

                >>> datapoints = [
                ...     {"timestamp": 150000000000, "value": 1000},
                ...     {"timestamp": 160000000000, "value": 2000},
                ... ]
                >>> c.time_series.data.insert(datapoints, external_id="def")

            Or they can be a Datapoints or DatapointsArray object (raw datapoints only)::

                >>> data = c.time_series.data.retrieve(external_id="abc", start="1w-ago", end="now")
                >>> c.time_series.data.insert(data, external_id="def")
        """
        post_dps_object = Identifier.of_either(id, external_id).as_dict()
        if isinstance(datapoints, (Datapoints, DatapointsArray)):
            if datapoints.value is None:
                raise ValueError(
                    "When inserting data using a `Datapoints` or `DatapointsArray` object, only raw datapoints are supported"
                )
            datapoints = list(zip(datapoints.timestamp, datapoints.value))  # type: ignore [arg-type]
        post_dps_object["datapoints"] = datapoints
        dps_poster = DatapointsPoster(self)
        dps_poster.insert([post_dps_object])

    def insert_multiple(self, datapoints: List[Dict[str, Union[str, int, List]]]) -> None:
        """`Insert datapoints into multiple time series <https://docs.cognite.com/api/v1/#operation/postMultiTimeSeriesDatapoints>`_

        Args:
            datapoints (List[Dict]): The datapoints you wish to insert along with the ids of the time series.
                See examples below.

        Returns:
            None

        Examples:

            Your datapoints can be a list of tuples where the first element is the timestamp and the second element is
            the value::

                >>> from cognite.client import CogniteClient
                >>> from datetime import datetime, timezone
                >>> c = CogniteClient()

                >>> datapoints = []
                >>> # With datetime objects and id
                >>> datapoints.append(
                ...     {"id": 1, "datapoints": [
                ...         (datetime(2018,1,1,tzinfo=timezone.utc), 1000),
                ...         (datetime(2018,1,2,tzinfo=timezone.utc), 2000)
                ... ]})
                >>> # with ms since epoch and externalId
                >>> datapoints.append({"externalId": 1, "datapoints": [(150000000000, 1000), (160000000000, 2000)]})
                >>> c.time_series.data.insert_multiple(datapoints)
        """
        dps_poster = DatapointsPoster(self)
        dps_poster.insert(datapoints)

    def delete_range(
        self, start: Union[int, str, datetime], end: Union[int, str, datetime], id: int = None, external_id: str = None
    ) -> None:
        """Delete a range of datapoints from a time series.

        Args:
            start (Union[int, str, datetime]): Inclusive start of delete range
            end (Union[int, str, datetime]): Exclusvie end of delete range
            id (int): Id of time series to delete data from
            external_id (str): External id of time series to delete data from

        Returns:
            None

        Examples:

            Deleting the last week of data from a time series::

                >>> from cognite.client import CogniteClient
                >>> c = CogniteClient()
                >>> c.time_series.data.delete_range(start="1w-ago", end="now", id=1)
        """
        start = timestamp_to_ms(start)
        end = timestamp_to_ms(end)
        assert end > start, "end must be larger than start"

        identifier = Identifier.of_either(id, external_id).as_dict()
        delete_dps_object = {**identifier, "inclusiveBegin": start, "exclusiveEnd": end}
        self._delete_datapoints_ranges([delete_dps_object])

    def delete_ranges(self, ranges: List[Dict[str, Any]]) -> None:
        """`Delete a range of datapoints from multiple time series. <https://docs.cognite.com/api/v1/#operation/deleteDatapoints>`_

        Args:
            ranges (List[Dict[str, Any]]): The list of datapoint ids along with time range to delete. See examples below.

        Returns:
            None

        Examples:

            Each element in the list ranges must be specify either id or externalId, and a range::

                >>> from cognite.client import CogniteClient
                >>> c = CogniteClient()
                >>> ranges = [{"id": 1, "start": "2d-ago", "end": "now"},
                ...             {"externalId": "abc", "start": "2d-ago", "end": "now"}]
                >>> c.time_series.data.delete_ranges(ranges)
        """
        valid_ranges = []
        for range in ranges:
            for key in range:
                if key not in ("id", "externalId", "start", "end"):
                    raise AssertionError(
                        "Invalid key '{}' in range. Must contain 'start', 'end', and 'id' or 'externalId".format(key)
                    )
            id = range.get("id")
            external_id = range.get("externalId")
            valid_range = Identifier.of_either(id, external_id).as_dict()
            start = timestamp_to_ms(range["start"])
            end = timestamp_to_ms(range["end"])
            valid_range.update({"inclusiveBegin": start, "exclusiveEnd": end})
            valid_ranges.append(valid_range)
        self._delete_datapoints_ranges(valid_ranges)

    def _delete_datapoints_ranges(self, delete_range_objects: List[Union[Dict]]) -> None:
        self._post(url_path=self._RESOURCE_PATH + "/delete", json={"items": delete_range_objects})

    def retrieve_dataframe(
        self,
        *,
        include_aggregate_name: bool = True,
        column_names: Literal["id", "external_id"] = "external_id",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Get datapoints directly in a pandas dataframe (convenience method wrapping the `retrieve` method).

        **Note**: If you have duplicated time series in your query, the dataframe columns will also contain duplicates.

        See `DatapointsAPI.retrieve` method for a description of the parameters, must be passed by keywords.

        Args:
            include_aggregate_name (bool): Include 'aggregate' in the column name, e.g. `my-ts|average`. Ignored for raw time series. Default: True
            column_names ("id" | "external_id"): Use either ids or external ids as column names. Time series missing external id will use id as backup. Default: "external_id"
            **kwargs (Any): Passed directly to `DatapointsAPI.retrieve`.

        Returns:
            pandas.DataFrame

        Examples:

            Get a pandas dataframe using a single id, and use this id as column name::

                >>> from cognite.client import CogniteClient
                >>> c = CogniteClient()
                >>> df = c.time_series.data.retrieve_dataframe(
                ...     id=12345,
                ...     start="2w-ago",
                ...     end="now",
                ...     column_names="id")

            Get a pandas dataframe containing the 'average' aggregate for two time series using a 30 day granularity,
            starting Jan 1, 1970 all the way up to present, without having the aggregate name in the column names::

                >>> df = c.time_series.data.retrieve_dataframe(
                ...     external_id=["foo", "bar"],
                ...     aggregates=["average"],
                ...     granularity="30d",
                ...     include_aggregate_name=False)
        """
        if column_names not in {"id", "external_id"}:
            raise ValueError(f"Given parameter {column_names=} must be one of 'id' or 'external_id'")

        if (dps_arr := self.retrieve(**kwargs)) is None:
            return pd.DataFrame(index=pd.to_datetime([]))
        return dps_arr.to_pandas(column_names, include_aggregate_name)

    def insert_dataframe(self, dataframe: pd.DataFrame, external_id_headers: bool = True, dropna: bool = True) -> None:
        """Insert a dataframe.

        The index of the dataframe must contain the timestamps. The names of the remaining columns specify the ids or external ids of
        the time series to which column contents will be written.

        Said time series must already exist.

        Args:
            dataframe (pandas.DataFrame):  Pandas DataFrame object containing the time series.
            external_id_headers (bool): Interpret the column names as external id. Pass False if using ids. Default: True.
            dropna (bool): Set to True to ignore NaNs in the given DataFrame, applied per column. Default: True.

        Returns:
            None

        Examples:
            Post a dataframe with white noise::

                >>> import numpy as np
                >>> import pandas as pd
                >>> from cognite.client import CogniteClient
                >>>
                >>> c = CogniteClient()
                >>> ts_xid = "my-foo-ts"
                >>> idx = pd.date_range(start="2018-01-01", periods=100, freq="1d")
                >>> noise = np.random.normal(0, 1, 100)
                >>> df = pd.DataFrame({ts_xid: noise}, index=idx)
                >>> c.time_series.data.insert_dataframe(df)
        """
        np = cast(Any, local_import("numpy"))
        if np.isinf(dataframe.select_dtypes(include=[np.number])).any(axis=None):
            raise ValueError("Dataframe contains one or more (+/-) Infinity. Remove them in order to insert the data.")
        if not dropna:
            if dataframe.isnull().any(axis=None):
                raise ValueError(
                    "Dataframe contains one or more NaNs. Remove or pass `dropna=True` in order to insert the data."
                )
        dps = []
        idx = dataframe.index.values.astype("datetime64[ms]").astype(np.int64)
        for column_id, col in dataframe.iteritems():
            mask = col.notna()
            datapoints = list(zip(idx[mask], col[mask]))
            if not datapoints:
                continue
            if external_id_headers:
                dps.append({"datapoints": datapoints, "externalId": column_id})
            else:
                dps.append({"datapoints": datapoints, "id": int(column_id)})
        self.insert_multiple(dps)


class DatapointsBin:
    def __init__(self, dps_objects_limit: int, dps_limit: int):
        self.dps_objects_limit = dps_objects_limit
        self.dps_limit = dps_limit
        self.current_num_datapoints = 0
        self.dps_object_list: List[dict] = []

    def add(self, dps_object: Dict[str, Any]) -> None:
        self.current_num_datapoints += len(dps_object["datapoints"])
        self.dps_object_list.append(dps_object)

    def will_fit(self, number_of_dps: int) -> bool:
        will_fit_dps = (self.current_num_datapoints + number_of_dps) <= self.dps_limit
        will_fit_dps_objects = (len(self.dps_object_list) + 1) <= self.dps_objects_limit
        return will_fit_dps and will_fit_dps_objects


class DatapointsPoster:
    def __init__(self, client: DatapointsAPI) -> None:
        self.client = client
        self.bins: List[DatapointsBin] = []

    def insert(self, dps_object_list: List[Dict[str, Any]]) -> None:
        valid_dps_object_list = self._validate_dps_objects(dps_object_list)
        binned_dps_object_lists = self._bin_datapoints(valid_dps_object_list)
        self._insert_datapoints_concurrently(binned_dps_object_lists)

    @staticmethod
    def _validate_dps_objects(dps_object_list: List[Dict[str, Any]]) -> List[dict]:
        valid_dps_objects = []
        for dps_object in dps_object_list:
            for key in dps_object:
                if key not in ("id", "externalId", "datapoints"):
                    raise ValueError(
                        "Invalid key '{}' in datapoints. Must contain 'datapoints', and 'id' or 'externalId".format(key)
                    )
            valid_dps_object = {k: dps_object[k] for k in ["id", "externalId"] if k in dps_object}
            valid_dps_object["datapoints"] = DatapointsPoster._validate_and_format_datapoints(dps_object["datapoints"])
            valid_dps_objects.append(valid_dps_object)
        return valid_dps_objects

    @staticmethod
    def _validate_and_format_datapoints(
        datapoints: Union[
            List[Dict[str, Any]],
            List[Tuple[Union[int, float, datetime], Union[int, float, str]]],
        ],
    ) -> List[Tuple[int, Any]]:
        assert_type(datapoints, "datapoints", [list])
        assert len(datapoints) > 0, "No datapoints provided"
        assert_type(datapoints[0], "datapoints element", [tuple, dict])

        valid_datapoints = []
        if isinstance(datapoints[0], tuple):
            valid_datapoints = [(timestamp_to_ms(t), v) for t, v in datapoints]
        elif isinstance(datapoints[0], dict):
            for dp in datapoints:
                dp = cast(Dict[str, Any], dp)
                assert "timestamp" in dp, "A datapoint is missing the 'timestamp' key"
                assert "value" in dp, "A datapoint is missing the 'value' key"
                valid_datapoints.append((timestamp_to_ms(dp["timestamp"]), dp["value"]))
        return valid_datapoints

    def _bin_datapoints(self, dps_object_list: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        for dps_object in dps_object_list:
            for i in range(0, len(dps_object["datapoints"]), DPS_LIMIT):
                dps_object_chunk = {k: dps_object[k] for k in ["id", "externalId"] if k in dps_object}
                dps_object_chunk["datapoints"] = dps_object["datapoints"][i : i + DPS_LIMIT]
                for bin in self.bins:
                    if bin.will_fit(len(dps_object_chunk["datapoints"])):
                        bin.add(dps_object_chunk)
                        break
                else:
                    bin = DatapointsBin(DPS_LIMIT, POST_DPS_OBJECTS_LIMIT)
                    bin.add(dps_object_chunk)
                    self.bins.append(bin)
        binned_dps_object_list = []
        for bin in self.bins:
            binned_dps_object_list.append(bin.dps_object_list)
        return binned_dps_object_list

    def _insert_datapoints_concurrently(self, dps_object_lists: List[List[Dict[str, Any]]]) -> None:
        tasks = []
        for dps_object_list in dps_object_lists:
            tasks.append((dps_object_list,))
        summary = execute_tasks_concurrently(
            self._insert_datapoints, tasks, max_workers=self.client._config.max_workers
        )
        summary.raise_compound_exception_if_failed_tasks(
            task_unwrap_fn=lambda x: x[0],
            task_list_element_unwrap_fn=lambda x: {k: x[k] for k in ["id", "externalId"] if k in x},
        )

    def _insert_datapoints(self, post_dps_objects: List[Dict[str, Any]]) -> None:
        # convert to memory intensive format as late as possible and clean up after
        for it in post_dps_objects:
            it["datapoints"] = [{"timestamp": t, "value": v} for t, v in it["datapoints"]]
        self.client._post(url_path=self.client._RESOURCE_PATH, json={"items": post_dps_objects})
        for it in post_dps_objects:
            del it["datapoints"]
