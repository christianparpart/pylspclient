from __future__ import print_function
import threading
import collections
from pylspclient import lsp_structs, JsonRpcEndpoint
from typing import Tuple, Dict, Union, Any

class LspEndpoint(threading.Thread):
    json_rpc_endpoint: JsonRpcEndpoint

    # TODO: notify_callbacks

    # TODO: method_callbacks

    # Contains a mapping from RPC call Id to condition variable
    # in order to noitify the caller of a the received response.
    event_dict: Dict[Any, threading.Condition] = {}

    # Contains a list of responses received for by an RPC call by given Id.
    # The dictionary key is the RPC Id.
    response_dict: Dict[Any, Tuple[Dict[str, Any], Dict[str, Any]]] = {}

    # Numberical Id to be used for the next RPC call.
    next_id: int = 0

    # RPC-call timeout in seconds.
    _timeout: float

    # Indicates the run()'s main loop to stop serving.
    shutdown_flag: bool = False

    def __init__(self, json_rpc_endpoint: JsonRpcEndpoint, method_callbacks={}, notify_callbacks={}, timeout: float=2):
        threading.Thread.__init__(self)
        self.json_rpc_endpoint = json_rpc_endpoint
        self.notify_callbacks = notify_callbacks
        self.method_callbacks = method_callbacks
        # self.event_dict = {} # Map from RPC Id to condition variable.
        # self.response_dict = {} # Map from RPC ID to Tuple[result, error]
        #self.next_id = 0
        self._timeout = timeout
        #self.shutdown_flag = False


    def handle_result(self, rpc_id: Any, result: Dict[str, Any], error: Dict[str, Any]):
        if not(rpc_id in self.event_dict):
            # Either rpc_id is None or we don't know this RPC Id. Silently drop.
            return
        self.response_dict[rpc_id] = (result, error)
        cond = self.event_dict[rpc_id]
        cond.acquire()
        cond.notify()
        cond.release()


    def stop(self):
        self.shutdown_flag = True


    def run(self):
        while not self.shutdown_flag:
            try:
                jsonrpc_message = self.json_rpc_endpoint.recv_response()
                if jsonrpc_message is None:
                    break
                method = jsonrpc_message.get("method")
                result = jsonrpc_message.get("result")
                error = jsonrpc_message.get("error")
                rpc_id: Union[int,str,None] = jsonrpc_message.get("id")
                params = jsonrpc_message.get("params")

                if method:
                    if rpc_id:
                        # a call for method
                        if method not in self.method_callbacks:
                            raise lsp_structs.ResponseError(lsp_structs.ErrorCodes.MethodNotFound, "Method not found: {method}".format(method=method))
                        result = self.method_callbacks[method](params)
                        self.send_response(rpc_id, result, None)
                    else:
                        # a call for notify
                        if method not in self.notify_callbacks:
                            # Have nothing to do with this.
                            print("Notify method not found: {method}.".format(method=method))
                        else:
                            self.notify_callbacks[method](params)
                else:
                    self.handle_result(rpc_id, result, error)
            except lsp_structs.ResponseError as e:
                self.send_response(rpc_id, None, e)


    def send_response(self, id: Union[int, str, None], result: Union[None, Dict[str, Any]], error: Union[None, Dict[str, Any]]):
        message_dict = {}
        message_dict["jsonrpc"] = "2.0"
        message_dict["id"] = id
        if result:
            message_dict["result"] = result
        if error:
            message_dict["error"] = error
        self.json_rpc_endpoint.send_request(message_dict)


    def send_message(self, method_name: str, params: Dict[str, Any], id: Any = None):
        message_dict = {}
        message_dict["jsonrpc"] = "2.0"
        if id is not None:
            message_dict["id"] = id
        message_dict["method"] = method_name
        message_dict["params"] = params
        self.json_rpc_endpoint.send_request(message_dict)


    def call_method(self, method_name: str, **kwargs):
        current_id = self.next_id
        self.next_id += 1
        cond = threading.Condition()
        self.event_dict[current_id] = cond

        cond.acquire()
        self.send_message(method_name, kwargs, current_id)
        if self.shutdown_flag:
            return None

        # Wait for reply by watching on the wakeup of the associated condition variable.
        if not cond.wait(timeout=self._timeout):
            raise TimeoutError()
        cond.release()

        self.event_dict.pop(current_id)
        result, error = self.response_dict.pop(current_id)

        if error:
            raise lsp_structs.ResponseError(error.get("code"), error.get("message"), error.get("data"))
        return result


    def send_notification(self, method_name, **kwargs):
        self.send_message(method_name, kwargs)
