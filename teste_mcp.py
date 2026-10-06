"""Chama o conector MCP por stdio, como o Claude faria.  Uso: python teste_mcp.py "consulta" ["nome de pasta"]"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

AQUI = os.path.dirname(os.path.abspath(__file__))


async def main(consulta, pasta):
    p = StdioServerParameters(command=os.path.join(AQUI, ".venv", "Scripts", "python.exe"),
                              args=["-X", "utf8", os.path.join(AQUI, "mcp_busca.py")])
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("ferramentas:", [t.name for t in (await s.list_tools()).tools])
            print("\n--- buscar_pastas ---\n" + (await s.call_tool("buscar_pastas", {"consulta": pasta, "limite": 3})).content[0].text)
            texto = (await s.call_tool("buscar_arquivos", {"consulta": consulta, "limite": 3})).content[0].text
            print("\n--- buscar_arquivos ---\n" + texto[:900])
            if texto[:2] == "1.":
                arq = texto.split("\n")[0].split(". ", 1)[1].rsplit("  (", 1)[0]
                res = await s.call_tool("ler_arquivo", {"arquivo": arq, "max_caracteres": 400})
                print("\n--- ler_arquivo ---\n" + res.content[0].text[:600])
            print("\n--- status_busca ---\n" + (await s.call_tool("status_busca", {})).content[0].text)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "contrato de prestação de serviços",
                     sys.argv[2] if len(sys.argv) > 2 else "contratos"))
