from pydantic import BaseModel, Field



class Loginrequest(BaseModel):
    email : str = Field (...,min_length = 5 , max_length= 100)
    
    password : str = Field (...,min_length = 5 ,max_length= 100)
    

class Loginresponse(BaseModel):
    access_token : str
    token_type : str
    customer_id : int
    first_name : str | None = None
    last_name : str | None = None
    session_id : str 
    