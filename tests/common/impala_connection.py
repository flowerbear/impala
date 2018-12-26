# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# Common for connections to Impala. Currently supports Beeswax connections and
# in the future will support HS2 connections. Provides tracing around all
# operations.

import abc
import logging
import re

import impala.dbapi as impyla
import tests.common
from tests.beeswax.impala_beeswax import ImpalaBeeswaxClient


LOG = logging.getLogger('impala_connection')
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
# All logging needs to be either executable SQL or a SQL comment (prefix with --).
console_handler.setFormatter(logging.Formatter('%(message)s'))
LOG.addHandler(console_handler)
LOG.propagate = False

# Regular expression that matches the "progress" entry in the HS2 log.
PROGRESS_LOG_RE = re.compile(
    r'^Query [a-z0-9:]+ [0-9]+% Complete \([0-9]+ out of [0-9]+\)$')

# Common wrapper around the internal types of HS2/Beeswax operation/query handles.
class OperationHandle(object):
  def __init__(self, handle, sql_stmt):
    self.__handle = handle
    self.__sql_stmt = sql_stmt

  def get_handle(self):
    return self.__handle

  def sql_stmt(self):
    return self.__sql_stmt


# Represents an Impala connection.
class ImpalaConnection(object):
  __metaclass__ = abc.ABCMeta
  @abc.abstractmethod
  def set_configuration_option(self, name, value):
    """Sets a configuraiton option name to the given value"""
    pass

  def set_configuration(self, config_option_dict):
    """Replaces existing configuration with the given dictionary"""
    assert config_option_dict is not None, "config_option_dict cannot be None"
    self.clear_configuration()
    for name, value in config_option_dict.iteritems():
      self.set_configuration_option(name, value)

  @abc.abstractmethod
  def clear_configuration(self):
    """Clears all existing configuration."""
    pass

  @abc.abstractmethod
  def get_default_configuration(self):
    """Return the default configuration for the connection, before any modifications are
    made to the session state. Returns a map with the config variable as the key and a
    string representation of the default value as the value."""
    pass

  @abc.abstractmethod
  def connect(self):
    """Opens the connection"""
    pass

  @abc.abstractmethod
  def close(self):
    """Closes the connection. Can be called multiple times"""
    pass

  @abc.abstractmethod
  def close_query(self, handle):
    """Closes the query."""
    pass

  @abc.abstractmethod
  def get_state(self, operation_handle):
    """Returns the state of a query"""
    pass

  @abc.abstractmethod
  def get_log(self, operation_handle):
    """Returns the log of an operation as a string, with entries separated by newlines."""
    pass

  @abc.abstractmethod
  def cancel(self, operation_handle):
    """Cancels an in-flight operation"""
    pass

  def execute(self, sql_stmt):
    """Executes a query and fetches the results"""
    pass

  @abc.abstractmethod
  def execute_async(self, sql_stmt):
    """Issues a query and returns the handle to the caller for processing. Only one
    async operation per connection at a time is supported, due to limitations of the
    Beeswax protocol and the Impyla client."""
    pass

  @abc.abstractmethod
  def fetch(self, sql_stmt, operation_handle, max_rows=-1):
    """Fetches query results up to max_rows given a handle and sql statement.
    If max_rows < 0, all rows are fetched. If max_rows > 0 but the number of
    rows returned is less than max_rows, all the rows have been fetched."""
    pass


# Represents a connection to Impala using the Beeswax API.
class BeeswaxConnection(ImpalaConnection):
  def __init__(self, host_port, use_kerberos=False, user=None, password=None,
               use_ssl=False):
    self.__beeswax_client = ImpalaBeeswaxClient(host_port, use_kerberos, user=user,
                                                password=password, use_ssl=use_ssl)
    self.__host_port = host_port
    self.QUERY_STATES = self.__beeswax_client.query_states

  def set_configuration_option(self, name, value):
    # Only set the option if it's not already set to the same value.
    if self.__beeswax_client.get_query_option(name) != value:
      LOG.info('SET %s=%s;' % (name, value))
      self.__beeswax_client.set_query_option(name, value)

  def get_default_configuration(self):
    result = {}
    for item in self.__beeswax_client.get_default_configuration():
      result[item.key] = item.value
    return result

  def clear_configuration(self):
    self.__beeswax_client.clear_query_options()
    # A hook in conftest sets tests.common.current_node.
    if hasattr(tests.common, "current_node"):
      self.set_configuration_option("client_identifier", tests.common.current_node)

  def connect(self):
    LOG.info("-- connecting to: %s" % self.__host_port)
    self.__beeswax_client.connect()

  # TODO: rename to close_connection
  def close(self):
    LOG.info("-- closing connection to: %s" % self.__host_port)
    self.__beeswax_client.close_connection()

  def close_query(self, operation_handle):
    LOG.info("-- closing query for operation handle: %s" % operation_handle)
    self.__beeswax_client.close_query(operation_handle.get_handle())

  def execute(self, sql_stmt, user=None):
    LOG.info("-- executing against %s\n%s;\n" % (self.__host_port, sql_stmt))
    return self.__beeswax_client.execute(sql_stmt, user=user)

  def execute_async(self, sql_stmt, user=None):
    LOG.info("-- executing async: %s\n%s;\n" % (self.__host_port, sql_stmt))
    beeswax_handle = self.__beeswax_client.execute_query_async(sql_stmt, user=user)
    return OperationHandle(beeswax_handle, sql_stmt)

  def cancel(self, operation_handle):
    LOG.info("-- canceling operation: %s" % operation_handle)
    return self.__beeswax_client.cancel_query(operation_handle.get_handle())

  def get_state(self, operation_handle):
    LOG.info("-- getting state for operation: %s" % operation_handle)
    return self.__beeswax_client.get_state(operation_handle.get_handle())

  def get_exec_summary(self, operation_handle):
    LOG.info("-- getting exec summary operation: %s" % operation_handle)
    return self.__beeswax_client.get_exec_summary(operation_handle.get_handle())

  def get_runtime_profile(self, operation_handle):
    LOG.info("-- getting runtime profile operation: %s" % operation_handle)
    return self.__beeswax_client.get_runtime_profile(operation_handle.get_handle())

  def wait_for_finished_timeout(self, operation_handle, timeout):
    LOG.info("-- waiting for query to reach FINISHED state: %s" % operation_handle)
    return self.__beeswax_client.wait_for_finished_timeout(
      operation_handle.get_handle(), timeout)

  def wait_for_admission_control(self, operation_handle):
    LOG.info("-- waiting for completion of the admission control processing of the "
        "query: %s" % operation_handle)
    return self.__beeswax_client.wait_for_admission_control(operation_handle.get_handle())

  def get_admission_result(self, operation_handle):
    LOG.info("-- getting the admission result: %s" % operation_handle)
    return self.__beeswax_client.get_admission_result(operation_handle.get_handle())

  def get_log(self, operation_handle):
    LOG.info("-- getting log for operation: %s" % operation_handle)
    return self.__beeswax_client.get_log(operation_handle.get_handle())

  def fetch(self, sql_stmt, operation_handle, max_rows = -1):
    LOG.info("-- fetching results from: %s" % operation_handle)
    return self.__beeswax_client.fetch_results(
        sql_stmt, operation_handle.get_handle(), max_rows)


class ImpylaHS2Connection(ImpalaConnection):
  """Connection to Impala using the impyla client connecting to HS2 endpoint.
  impyla implements the standard Python dbabi: https://www.python.org/dev/peps/pep-0249/
  plus Impala-specific extensions, e.g. for fetching runtime profiles.
  TODO: implement support for kerberos, SSL, etc.
  """
  def __init__(self, host_port, use_kerberos=False):
    self.__host_port = host_port
    if use_kerberos:
      raise NotImplementedError("Kerberos support not yet implemented")
    # Impyla connection and cursor is initialised in connect(). We need to reuse the same
    # cursor for different operations (as opposed to creating a new cursor per operation)
    # so that the session is preserved. This means that we can only execute one operation
    # at a time per connection, which is a limitation also imposed by the Beeswax API.
    self.__impyla_conn = None
    self.__cursor = None
    # Query options to send along with each query.
    self.__query_options = {}

  def set_configuration_option(self, name, value):
    self.__query_options[name] = str(value)

  def get_default_configuration(self):
    return self.__default_query_options.copy()

  def clear_configuration(self):
    self.__query_options.clear()
    if hasattr(tests.common, "current_node"):
      self.set_configuration_option("client_identifier", tests.common.current_node)

  def connect(self):
    LOG.info("-- connecting to {0} with impyla".format(self.__host_port))
    host, port = self.__host_port.split(":")
    self.__impyla_conn = impyla.connect(host=host, port=int(port))
    LOG.info("Conn {0}".format(self.__impyla_conn))
    # Get the default query options for the session before any modifications are made.
    self.__cursor = self.__impyla_conn.cursor()
    self.__cursor.execute("set all")
    self.__default_query_options = {}
    for name, val, _ in self.__cursor:
      self.__default_query_options[name] = val
    self.__cursor.close_operation()
    LOG.debug("Default query options: {0}".format(self.__default_query_options))

  def close(self):
    LOG.info("-- closing connection to: {0}".format(self.__host_port))
    self.__impyla_conn.close()

  def close_query(self, operation_handle):
    LOG.info("-- closing query for operation handle: {0}".format(operation_handle))
    operation_handle.get_handle().close_operation()

  def execute(self, sql_stmt, user=None):
    handle = self.execute_async(sql_stmt, user)
    try:
      return self.__fetch_results(handle)
    finally:
      self.close_query(handle)

  def execute_async(self, sql_stmt, user=None):
    LOG.info("-- executing: {0}\n{1};\n".format(self.__host_port, sql_stmt))
    if user is not None:
      raise NotImplementedError("Not yet implemented for HS2 - authentication")
    try:
      self.__cursor.execute(sql_stmt, configuration=self.__query_options)
      return OperationHandle(self.__cursor, sql_stmt)
    except Exception:
      self.__cursor.close_operation()
      raise

  def cancel(self, operation_handle):
    LOG.info("-- canceling operation: {0}".format(operation_handle))
    return self.__beeswax_client.cancel_query(operation_handle.get_handle())

  def get_state(self, operation_handle):
    LOG.info("-- getting state for operation: {0}".format(operation_handle))
    raise NotImplementedError("Not yet implemented for HS2 - states differ from beeswax")

  def get_exec_summary(self, operation_handle):
    LOG.info("-- getting exec summary operation: {0}".format(operation_handle))
    raise NotImplementedError(
        "Not yet implemented for HS2 - summary returned is thrift, not string.")

  def get_runtime_profile(self, operation_handle):
    LOG.info("-- getting runtime profile operation: {0}".format(operation_handle))
    return self.__cursor.get_profile()

  def wait_for_finished_timeout(self, operation_handle, timeout):
    LOG.info("-- waiting for query to reach FINISHED state: {0}".format(operation_handle))
    raise NotImplementedError("Not yet implemented for HS2 - states differ from beeswax")

  def wait_for_admission_control(self, operation_handle):
    LOG.info("-- waiting for completion of the admission control processing of the "
        "query: {0}".format(operation_handle))
    raise NotImplementedError("Not yet implemented for HS2 - states differ from beeswax")

  def get_admission_result(self, operation_handle):
    LOG.info("-- getting the admission result: {0}".format(operation_handle))
    raise NotImplementedError("Not yet implemented for HS2 - states differ from beeswax")

  def get_log(self, operation_handle):
    LOG.info("-- getting log for operation: {0}".format(operation_handle))
    # HS2 includes non-error log messages that we need to filter out.
    cursor = operation_handle.get_handle()
    lines = [line for line in cursor.get_log().split('\n')
             if not PROGRESS_LOG_RE.match(line)]
    return '\n'.join(lines)

  def fetch(self, sql_stmt, handle, max_rows=-1):
    LOG.info("-- fetching results from: {0}".format(handle))
    return self.__fetch_results(handle, max_rows)

  def __fetch_results(self, handle, max_rows=-1):
    """Implementation of result fetching from handle."""
    cursor = handle.get_handle()
    assert cursor is not None
    # Don't fetch data for queries with no results.
    result_tuples = None
    column_labels = None
    column_types = None
    if cursor.has_result_set:
      desc = cursor.description
      column_labels = [col_desc[0].upper() for col_desc in desc]
      column_types = [col_desc[1].upper() for col_desc in desc]
      if max_rows < 0:
        result_tuples = cursor.fetchall()
      else:
        result_tuples = cursor.fetchmany(max_rows)
    log = self.get_log(handle)
    profile = self.get_runtime_profile(handle)
    return ImpylaHS2ResultSet(success=True, result_tuples=result_tuples,
                              column_labels=column_labels, column_types=column_types,
                              query=handle.sql_stmt(), log=log, profile=profile)


class ImpylaHS2ResultSet(object):
  """This emulates the interface of ImpalaBeeswaxResult so that it can be used in
  place of it. TODO: when we deprecate/remove Beeswax, clean this up."""
  def __init__(self, success, result_tuples, column_labels, column_types, query, log,
      profile):
    self.success = success
    self.column_labels = column_labels
    self.column_types = column_types
    self.query = query
    self.log = log
    self.profile = profile
    self.__result_tuples = result_tuples
    # self.data is the data in the ImpalaBeeswaxResult format: a list of rows with each
    # row represented as a tab-separated string.
    self.data = None
    if result_tuples is not None:
      self.data = [self.__convert_result_row(tuple) for tuple in result_tuples]

  def __convert_result_row(self, result_tuple):
    """Take primitive values from a result tuple and construct the tab-separated string
    that would have been returned via beeswax."""
    return '\t'.join([self.__convert_result_value(val, type)
                      for val, type in zip(result_tuple, self.column_types)])

  def __convert_result_value(self, val, type):
    """Take a primitive value from a result tuple and its type and construct the string
    that would have been returned via beeswax."""
    if val is None:
      return 'NULL'
    else:
      return str(val)


def create_connection(host_port, use_kerberos=False, protocol='beeswax'):
  if protocol == 'beeswax':
    c = BeeswaxConnection(host_port=host_port, use_kerberos=use_kerberos)
  else:
    assert protocol == 'hs2'
    c = ImpylaHS2Connection(host_port=host_port, use_kerberos=use_kerberos)

  # A hook in conftest sets tests.common.current_node.
  if hasattr(tests.common, "current_node"):
    c.set_configuration_option("client_identifier", tests.common.current_node)
  return c

def create_ldap_connection(host_port, user, password, use_ssl=False):
  return BeeswaxConnection(host_port=host_port, user=user, password=password,
                           use_ssl=use_ssl)
