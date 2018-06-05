# -*- coding: utf-8 -*-
"""Depthseries Module

This module mirrors the Timeseries API, but handles pairs of timeseries to emulate depth(or other indexed) data series. It allows you to fetch data from the api and output it in various formats.

https://doc.cognitedata.com/0.5/#Cognite-API-Time-series
"""

import sys

from typing import List
import copy
import itertools
from urllib.parse import quote_plus

import cognite._utils as _utils
import cognite.config as config

from cognite.v05 import timeseries
from cognite.v05.dto import LatestDatapointResponse, \
    DatapointDepth, \
    TimeSeries, TimeSeriesResponse, Datapoint


def post_datapoints(name, depthdatapoints: List[DatapointDepth], **kwargs):
    '''Insert a list of datapoints.

    Args:
        name (str):       Name of timeseries to insert to.

        datapoints (list[v05.data_objects.Datapoint): List of datapoint data transfer objects to insert.

    Keyword Args:
        api_key (str): Your api-key.

        project (str): Project name.

    Returns:
        An empty response.
    '''
    msIncrement = 1000
    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    try:
        res = timeseries.get_latest(name)
        offset = res.to_json()['timestamp'] + msIncrement
    except:
        offset = 0  # Random timestamp to start the time series

    url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries/data/{}'.format(project, quote_plus(name))
    urldepth = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries/data/{}'.format(project, quote_plus(
        _generateIndexName(name)))

    headers = {
        'api-key': api_key,
        'content-type': 'application/json',
        'accept': 'application/json'
    }
    datapoints = []
    depthpoints = []
    for datapoint in depthdatapoints:
        datapoints.append(Datapoint(offset, datapoint.value))
        depthpoints.append(Datapoint(offset, datapoint.depth))
        offset += msIncrement

    ul_dps_limit = 100000
    i = 0
    while i < len(datapoints):
        body = {'items': [dp.__dict__ for dp in datapoints[i:i + ul_dps_limit]]}
        _utils.post_request(url, body=body, headers=headers)
        body = {'items': [dp.__dict__ for dp in depthpoints[i:i + ul_dps_limit]]}
        res = _utils.post_request(urldepth, body=body, headers=headers)
        i += ul_dps_limit

    return res.json()


def get_latest(name, **kwargs):
    '''Returns a LatestDatapointObject containing the latest datapoint for the given depthseries.

    Args:
        name (str):       The name of the depthseries to retrieve data for.

    Keyword Arguments:
        api_key (str):          Your api-key.
        project (str):          Project name.

    Returns:
        v05.data_objects.LatestDatapointsResponse: A data object containing the requested data with several getter methods with different
        output formats.
    '''
    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries/latest/{}'.format(project, quote_plus(name))
    headers = {
        'api-key': api_key,
        'accept': 'application/json'
    }
    res = _utils.get_request(url, headers=headers, cookies=config.get_cookies())
    return LatestDatapointResponse(res.json())


def get_depthseries(prefix=None, description=None, include_metadata=False, asset_id=None, path=None, **kwargs):
    '''Returns a TimeSeriesObject containing the requested series.

    Args:
        prefix (str):           List timeseries with this prefix in the name.

        description (str):      Filter timeseries taht contains this string in its description.

        include_metadata (bool):    Decide if the metadata field should be returned or not. Defaults to False.

        asset_id (int):        Get timeseries related to this asset.

        path (str):             Get timeseries under this asset path branch.

    Keyword Arguments:
        limit (int):            Number of results to return.

        api_key (str):          Your api-key.

        project (str):          Project name.

        autopaging (bool):      Whether or not to automatically page through results. If set to true, limit will be
                                disregarded. Defaults to False.

    Returns:
        v05.data_objects.TimeSeriesResponse: A data object containing the requested timeseries with several getter methods with different
        output formats.
    '''
    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries'.format(project)
    headers = {
        'api-key': api_key,
        'accept': 'application/json'
    }
    params = {
        'q': prefix,
        'description': description,
        'includeMetadata': include_metadata,
        'assetId': asset_id,
        'path': path,
        'limit': kwargs.get('limit', 10000) if not kwargs.get('autopaging') else 10000
    }

    timeseries = []
    res = _utils.get_request(url=url, headers=headers, params=params, cookies=config.get_cookies())
    timeseries.extend([ts for ts in res.json()['data']['items']])
    next_cursor = res.json()['data'].get('nextCursor')

    while next_cursor and kwargs.get('autopaging'):
        params['cursor'] = next_cursor
        res = _utils.get_request(url=url, headers=headers, params=params, cookies=config.get_cookies())
        timeseries.extend([ts for ts in res.json()['data']['items'] if not ts['name'].endswith(_generateIndexName(""))])
        next_cursor = res.json()['data'].get('nextCursor')

    return TimeSeriesResponse(
        {'data': {'nextCursor': next_cursor, 'previousCursor': res.json()['data'].get('previousCursor'),
                  'items': timeseries}})


def _generateIndexName(depthSeriesName):
    return depthSeriesName + "_DepthIndex"


def post_depth_series(depth_series: List[TimeSeries], **kwargs):
    '''Create a new depth series.

    Args:
        depth_series (list[v05.dto.TimeSeries]):   List of time series data transfer objects to create.
        Corresponding depth series used for indexing will be created automatically, with unit of m(meter)

    Keyword Args:
        api_key (str): Your api-key.

        project (str): Project name.
    Returns:
        An empty response.
    '''

    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries'.format(project)
    depth_indexes = copy.deepcopy(depth_series)
    for ts in depth_indexes:
        ts.name = _generateIndexName(ts.name)
        ts.unit = "m"
        ts.isString = False

    body = {
        'items': [ts.__dict__ for ts in itertools.chain(depth_series, depth_indexes)]
    }

    headers = {
        'api-key': api_key,
        'content-type': 'application/json',
        'accept': 'application/json'
    }
    try:
        _utils.post_request(url, body=body, headers=headers)
    except _utils.APIError as e:
        # Are we getting this error because some metrics already exist? If so, then we still want to create the rest
        if "Some metrics already exist" in str(e):
            # To avoid parsing the error to figure out which metrics are missing, naively create each one in turn and
            # ignore the "Some metrics already exist" error if it happens again
            for ts in itertools.chain(depth_series, depth_indexes):
                body = {
                    'items': [ts.__dict__]
                }
                try:
                    _utils.post_request(url, body=body, headers=headers)
                except _utils.APIError as e:
                    if "Some metrics already exist" in str(e):
                        continue
                    else:
                        raise e
        else:
            raise e
    return {}


def _has_depth_index_changes(ds: TimeSeries) -> bool:
    if ds.metadata is None and ds.assetId is None and ds.description is None and ds.security_categories is None and ds.step is None:
        return False
    return True


def update_depth_series(depth_series: List[TimeSeries], **kwargs):
    '''Update an existing time series.

    For each field that can be updated, a null value indicates that nothing should be done.

    Args:
        depth_series (list[v05.dto.TimeSeries]):   List of time series data transfer objects to update.

    Keyword Args:
        api_key (str): Your api-key.

        project (str): Project name.

    Returns:
        An empty response.
    '''

    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries'.format(project)

    body = {
        'items': [ts.__dict__ for ts in depth_series]
    }

    headers = {
        'api-key': api_key,
        'content-type': 'application/json',
        'accept': 'application/json'
    }

    res = _utils.put_request(url, body=body, headers=headers)
    if res.json() == {}:
        for dsdto in depth_series:
            dsdto.name = _generateIndexName(dsdto.name)
            dsdto.isString = None
            dsdto.unit = None
        items = [ts.__dict__ for ts in depth_series if _has_depth_index_changes(ts)]
        body = {
            'items': items
        }
        if len(items) > 0:
            res = _utils.put_request(url, body=body, headers=headers)
    return res.json()


def delete_depth_series(name, **kwargs):
    '''Delete a depthseries.

    Args:
        name (str):   Name of depthseries to delete.

    Keyword Args:
        api_key (str): Your api-key.

        project (str): Project name.

    Returns:
        An empty response.
    '''
    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries/{}'.format(project, name)

    headers = {
        'api-key': api_key,
        'accept': 'application/json'
    }

    res = _utils.delete_request(url, headers=headers)
    if res.json() == {}:
        url = config.get_base_url(api_version=0.5) + '/projects/{}/timeseries/{}'.format(project,
                                                                                         _generateIndexName(name))
        res = _utils.delete_request(url, headers=headers)

    return res.json()


def reset_depth_series(name, **kwargs):
    '''Delete all datapoints for a depthseries.

       Args:
           name (str):   Name of depthseries to delete.

       Keyword Args:
           api_key (str): Your api-key.

           project (str): Project name.

       Returns:
           An empty response.
       '''
    api_key, project = config.get_config_variables(kwargs.get('api_key'), kwargs.get('project'))
    url = config.get_base_url(
        api_version=0.5) + '/projects/{}/timeseries/{}?timestampInclusiveBegin=0?timestampInclusiveEnd={}'.format(
        project, quote_plus(name), sys.maxsize)

    headers = {
        'api-key': api_key,
        'accept': 'application/json'
    }
    res = _utils.delete_request(url, headers=headers)
    if res == {}:
        url = config.get_base_url(
            api_version=0.5) + '/projects/{}/timeseries/{}?timestampInclusiveBegin=0?timestampInclusiveEnd={}'.format(
            project, quote_plus(_generateIndexName(name)), sys.maxsize)
        res = _utils.delete_request(url, headers=headers)

    return res.json()
