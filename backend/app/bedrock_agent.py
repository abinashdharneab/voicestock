import os
import json
import boto3
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from . import tools

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = "amazon.nova-lite-v1:0"

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


# ---------- TOOL DEFINITIONS (schema Bedrock will see) ----------
TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "check_item",
                "description": "Check how much stock is left for a product by name.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "product_name": {"type": "string", "description": "Name of the product"}
                        },
                        "required": ["product_name"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "get_expiring_items",
                "description": "Get products that are expiring within a number of days.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "within_days": {"type": "integer", "description": "Number of days to look ahead, default 7"}
                        },
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "get_low_stock",
                "description": "Get all products that are below their minimum stock level.",
                "inputSchema": {
                    "json": {"type": "object", "properties": {}}
                },
            }
        },
        {
            "toolSpec": {
                "name": "add_stock",
                "description": "Add new stock for a product. Creates the product if it doesn't exist.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "product_name": {"type": "string"},
                            "quantity": {"type": "integer"},
                            "expiry_date": {"type": "string", "description": "Format YYYY-MM-DD, optional"},
                            "cost_price": {"type": "number", "description": "optional"},
                            "unit": {"type": "string", "description": "optional, e.g. packet, piece"},
                        },
                        "required": ["product_name", "quantity"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "record_sale",
                "description": "Record a sale, reducing stock. Use when a customer buys something.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "product_name": {"type": "string"},
                            "quantity": {"type": "integer"},
                            "bill_id": {"type": "string", "description": "optional unique bill id"},
                        },
                        "required": ["product_name", "quantity"],
                    }
                },
            }
        },
    ]
}


# ---------- MAP tool name -> actual python function ----------
def execute_tool(db: Session, tool_name: str, tool_input: dict):
    if tool_name == "check_item":
        return tools.check_item(db, tool_input.get("product_name", ""))
    elif tool_name == "get_expiring_items":
        return tools.get_expiring_items(db, tool_input.get("within_days", 7))
    elif tool_name == "get_low_stock":
        return tools.get_low_stock(db)
    elif tool_name == "add_stock":
        return tools.add_stock(
            db,
            tool_input.get("product_name", ""),
            tool_input.get("quantity", 0),
            tool_input.get("expiry_date"),
            tool_input.get("cost_price"),
            tool_input.get("unit", "packet"),
        )
    elif tool_name == "record_sale":
        return tools.record_sale(
            db,
            tool_input.get("product_name", ""),
            tool_input.get("quantity", 0),
            tool_input.get("bill_id"),
        )
    else:
        return {"error": f"Unknown tool: {tool_name}"}


# ---------- MAIN CHAT FUNCTION ----------
def chat_with_agent(db: Session, user_message: str):
    messages = [{"role": "user", "content": [{"text": user_message}]}]

    system_prompt = [{
        "text": (
            "You are VoiceStock, a helpful voice assistant for a small shop owner. "
            "You help check inventory, expiring items, low stock, add new stock, and record sales. "
            "Always use the tools provided to answer questions about stock. "
            "Keep your spoken replies short, natural, and conversational, like you're talking to someone in person."
        )
    }]

    while True:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=messages,
            system=system_prompt,
            toolConfig=TOOL_CONFIG,
        )

        output_message = response["output"]["message"]
        messages.append(output_message)

        stop_reason = response.get("stopReason")

        if stop_reason == "tool_use":
            tool_results = []
            for content_block in output_message["content"]:
                if "toolUse" in content_block:
                    tool_use = content_block["toolUse"]
                    tool_name = tool_use["name"]
                    tool_input = tool_use["input"]
                    tool_use_id = tool_use["toolUseId"]

                    result = execute_tool(db, tool_name, tool_input)

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"json": result}],
                        }
                    })

            messages.append({"role": "user", "content": tool_results})
            continue

        # final answer
        final_text = ""
        for block in output_message["content"]:
            if "text" in block:
                final_text += block["text"]

        return final_text