import os

from dotenv import load_dotenv

from sqlalchemy import create_engine, event

from sqlalchemy.orm import sessionmaker,declarative_base

from sqlalchemy.engine import URL
from sqlalchemy import text

from app.utilities.log import Applogger


load_dotenv()

logger = Applogger.get_loggerdata(__name__)


server = os.getenv("DB_SERVER")
database = os.getenv("DB_NAME")
username = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")

connection_url = URL.create(
    "mssql+pyodbc",
    username=username,
    password=password,
    host=server,
    database=database,
    query={
        "driver": driver,
        "TrustServerCertificate": "yes",
    },
)

engine = create_engine(
    connection_url,
    pool_size=5,
    max_overflow = 10,
    pool_timeout = 30,
    pool_recycle = 1800,
    pool_pre_ping = True,
    # Keeps customer ids and GPS values out of error messages.
    hide_parameters = True
    
)


@event.listens_for(engine, "handle_error")
def log_database_error(context):
    """Log every database error from every tool and service."""
    statement = " ".join((context.statement or "").split())

    logger.error(
        "database_error type=%s error=%s statement=%s",
        type(context.original_exception).__name__,
        context.original_exception,
        statement[:300],
    )


sessionlocalcreate = sessionmaker(
    bind= engine,autocommit = False,autoflush= False
)

basedata = declarative_base()

def get_connection():
    db = sessionlocalcreate()
    
    try:
        yield db
        
    finally:
        db.close()
    

