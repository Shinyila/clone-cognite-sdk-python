# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: data_point_list_response.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


import cognite.client._proto_legacy.data_points_pb2 as data__points__pb2


DESCRIPTOR = _descriptor.FileDescriptor(
  name='data_point_list_response.proto',
  package='com.cognite.v1.timeseries.proto',
  syntax='proto3',
  serialized_options=b'P\001',
  create_key=_descriptor._internal_create_key,
  serialized_pb=b'\n\x1e\x64\x61ta_point_list_response.proto\x12\x1f\x63om.cognite.v1.timeseries.proto\x1a\x11\x64\x61ta_points.proto\"\xfd\x02\n\x11\x44\x61taPointListItem\x12\n\n\x02id\x18\x01 \x01(\x03\x12\x12\n\nexternalId\x18\x02 \x01(\t\x12\x10\n\x08isString\x18\x06 \x01(\x08\x12\x0e\n\x06isStep\x18\x07 \x01(\x08\x12\x0c\n\x04unit\x18\x08 \x01(\t\x12\x12\n\nnextCursor\x18\t \x01(\t\x12O\n\x11numericDatapoints\x18\x03 \x01(\x0b\x32\x32.com.cognite.v1.timeseries.proto.NumericDatapointsH\x00\x12M\n\x10stringDatapoints\x18\x04 \x01(\x0b\x32\x31.com.cognite.v1.timeseries.proto.StringDatapointsH\x00\x12S\n\x13\x61ggregateDatapoints\x18\x05 \x01(\x0b\x32\x34.com.cognite.v1.timeseries.proto.AggregateDatapointsH\x00\x42\x0f\n\rdatapointType\"Z\n\x15\x44\x61taPointListResponse\x12\x41\n\x05items\x18\x01 \x03(\x0b\x32\x32.com.cognite.v1.timeseries.proto.DataPointListItemB\x02P\x01\x62\x06proto3'
  ,
  dependencies=[data__points__pb2.DESCRIPTOR,])




_DATAPOINTLISTITEM = _descriptor.Descriptor(
  name='DataPointListItem',
  full_name='com.cognite.v1.timeseries.proto.DataPointListItem',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='id', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.id', index=0,
      number=1, type=3, cpp_type=2, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='externalId', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.externalId', index=1,
      number=2, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='isString', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.isString', index=2,
      number=6, type=8, cpp_type=7, label=1,
      has_default_value=False, default_value=False,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='isStep', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.isStep', index=3,
      number=7, type=8, cpp_type=7, label=1,
      has_default_value=False, default_value=False,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='unit', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.unit', index=4,
      number=8, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='nextCursor', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.nextCursor', index=5,
      number=9, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='numericDatapoints', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.numericDatapoints', index=6,
      number=3, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='stringDatapoints', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.stringDatapoints', index=7,
      number=4, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='aggregateDatapoints', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.aggregateDatapoints', index=8,
      number=5, type=11, cpp_type=10, label=1,
      has_default_value=False, default_value=None,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
    _descriptor.OneofDescriptor(
      name='datapointType', full_name='com.cognite.v1.timeseries.proto.DataPointListItem.datapointType',
      index=0, containing_type=None,
      create_key=_descriptor._internal_create_key,
    fields=[]),
  ],
  serialized_start=87,
  serialized_end=468,
)


_DATAPOINTLISTRESPONSE = _descriptor.Descriptor(
  name='DataPointListResponse',
  full_name='com.cognite.v1.timeseries.proto.DataPointListResponse',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='items', full_name='com.cognite.v1.timeseries.proto.DataPointListResponse.items', index=0,
      number=1, type=11, cpp_type=10, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=470,
  serialized_end=560,
)

_DATAPOINTLISTITEM.fields_by_name['numericDatapoints'].message_type = data__points__pb2._NUMERICDATAPOINTS
_DATAPOINTLISTITEM.fields_by_name['stringDatapoints'].message_type = data__points__pb2._STRINGDATAPOINTS
_DATAPOINTLISTITEM.fields_by_name['aggregateDatapoints'].message_type = data__points__pb2._AGGREGATEDATAPOINTS
_DATAPOINTLISTITEM.oneofs_by_name['datapointType'].fields.append(
  _DATAPOINTLISTITEM.fields_by_name['numericDatapoints'])
_DATAPOINTLISTITEM.fields_by_name['numericDatapoints'].containing_oneof = _DATAPOINTLISTITEM.oneofs_by_name['datapointType']
_DATAPOINTLISTITEM.oneofs_by_name['datapointType'].fields.append(
  _DATAPOINTLISTITEM.fields_by_name['stringDatapoints'])
_DATAPOINTLISTITEM.fields_by_name['stringDatapoints'].containing_oneof = _DATAPOINTLISTITEM.oneofs_by_name['datapointType']
_DATAPOINTLISTITEM.oneofs_by_name['datapointType'].fields.append(
  _DATAPOINTLISTITEM.fields_by_name['aggregateDatapoints'])
_DATAPOINTLISTITEM.fields_by_name['aggregateDatapoints'].containing_oneof = _DATAPOINTLISTITEM.oneofs_by_name['datapointType']
_DATAPOINTLISTRESPONSE.fields_by_name['items'].message_type = _DATAPOINTLISTITEM
DESCRIPTOR.message_types_by_name['DataPointListItem'] = _DATAPOINTLISTITEM
DESCRIPTOR.message_types_by_name['DataPointListResponse'] = _DATAPOINTLISTRESPONSE
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

DataPointListItem = _reflection.GeneratedProtocolMessageType('DataPointListItem', (_message.Message,), {
  'DESCRIPTOR' : _DATAPOINTLISTITEM,
  '__module__' : 'data_point_list_response_pb2'
  # @@protoc_insertion_point(class_scope:com.cognite.v1.timeseries.proto.DataPointListItem)
  })
_sym_db.RegisterMessage(DataPointListItem)

DataPointListResponse = _reflection.GeneratedProtocolMessageType('DataPointListResponse', (_message.Message,), {
  'DESCRIPTOR' : _DATAPOINTLISTRESPONSE,
  '__module__' : 'data_point_list_response_pb2'
  # @@protoc_insertion_point(class_scope:com.cognite.v1.timeseries.proto.DataPointListResponse)
  })
_sym_db.RegisterMessage(DataPointListResponse)


DESCRIPTOR._options = None
# @@protoc_insertion_point(module_scope)
