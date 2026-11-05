"""Identifiers taken from the source documents. Not inferred from a live call."""

SQL_PACK_VERSION = "0.3.0"
API_ROUTE = "/api/vgg/finance/v1.0"

# Sandbox workbook. Elijah must confirm this id belongs to the instance he opens.
ADVANCLY_COMPANY_ID = "74c0cf04-6359-ef11-a85a-7c1e52340759"
ADVANCLY_WORKBOOK_NAME = "Advancly Technologies NIG Ltd."
ADVANCLY_ENTITY_CODE = "ADVANCLY"

DEV_ISSUER = "https://vggos.vgg.tools/dev-login"
DEV_SUBJECT = "dev-operator"
SECRET_REFERENCE = "file://local-vggos-secrets#VGGOS_BC_ACCESS_KEY"

# Cost and revenue center extract for Advancly. Dimension values, not accounts.
# ADLC020 is spelled Advacly in the extract. ADL040 is not in the extract.
ADVANCLY_DIMENSION_CODES = (
    ("Cost", "ADLC010", "Advancly_Spark_Interest Exp"),
    ("Cost", "ADLC020", "Advacly_Fuse_Interest Exp"),
    ("Cost", "ADLC030", "Advancly_Serve_Interest Exp"),
    ("Cost", "ADLC040", "Advancly_Trade_Interest Exp"),
    ("Cost", "ADLC050", "Advancly_Embed_Interest Exp"),
    ("Revenue", "ADL010", "Advancly_Spark"),
    ("Revenue", "ADL020", "Advancly_Fuse"),
    ("Revenue", "ADL030", "Advancly_Serve"),
    ("Revenue", "ADL050", "Advancly_Embed"),
)

MASTER_STREAMS = (
    "generalLedgerSetups",
    "accountingPeriods",
    "glAccounts",
    "dimensions",
    "dimensionValues",
    "dimensionSetEntries",
    "bankAccounts",
)

REQUIRED_ENTITY_SETS = MASTER_STREAMS + ("glEntries", "glBalancesByPeriod")
