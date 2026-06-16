import os
from py_clob_client_v2 import AssetType, ClobClient, OrderArgs, PartialCreateOrderOptions
from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, OrderPayload
from py_clob_client_v2.order_builder.constants import BUY


def gen_api_key():
    tempClient = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=os.environ["POLYMARKET_PRIVATE_KEY"],
        use_server_time=True,
    )

    apiCreds = tempClient.derive_api_key()
    return apiCreds

def delete_api_key(apiCreds: ApiCreds):
    tempClient = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=os.environ["POLYMARKET_PRIVATE_KEY"],
        creds=apiCreds,
        use_server_time=True,
    )

    tempClient.delete_api_key()

def get_api_keys(apiCreds: ApiCreds):
    tempClient = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=os.environ["POLYMARKET_PRIVATE_KEY"],
        creds=apiCreds,
        use_server_time=True,
    )

    return tempClient.get_api_keys()

def get_client(apiCreds: ApiCreds):
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=os.environ["POLYMARKET_PRIVATE_KEY"],
        creds=apiCreds,
        signature_type=3,
        funder="0x06F135b95584505381E505698030B338E81b8BfE",
        use_server_time=True,
    )
    return client


apiCreds = gen_api_key()
client = get_client(apiCreds)


# collateral = client.get_balance_allowance(
#     BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
# )
# print(collateral)

# order = client.create_and_post_order(
#     OrderArgs(
#         token_id="106358199928543572722365776921430573322650186446577569494084373051420688646656",
#         price=0.01,
#         size=5,
#         side=BUY,
#     ),
#     options=PartialCreateOrderOptions(
#         tick_size="0.01",
#         neg_risk=False,
#     ),
#     post_only=True,
# )
# print("order:", order)

# orderId = "0x4e2432be1e06fd18fc5e582bc38e3a6e6abc47d2a8dcf91da2185526ff8034a0"
# print(client.cancel_order(OrderPayload(orderID=orderId)))

orders = client.get_open_orders()
print("open orders count:", len(orders))
print(orders)