import os

from dotenv import load_dotenv

from sqlalchemy import create_engine

from sqlalchemy.orm import sessionmaker,declarative_base

from sqlalchemy.engine import URL


load_dotenv


server = os.getenv("DB_SERVER")
database = os.getenv("DB_NAME")
username = os.getenv("DB_USERNAME")
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
    pool_pre_ping = True
    
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
    

