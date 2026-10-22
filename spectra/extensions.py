"""
Shared Flask extensions — initialised without app (app-factory pattern).
"""
from flask_pymongo import PyMongo

mongo = PyMongo()
