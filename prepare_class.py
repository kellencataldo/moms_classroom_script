##############################################################################################
# This script is designed to help automate several tedious tasks in my mother's daily workflow.
# Because of the quarantine lockdown, she has switched to teaching through google classrooms.
# The steps which this script is trying to automate are as follows: 
#   1. Copy several template files in her google drive
#       - These template files should have their names postfixed with the day of the week
#   2. Create a new coursework assignment in her google class for every one of these files
#       - These coursework assignments should mirror the names of the copied drive files
#   3. Attach the copied drive file to the corresponding google class assignment
#   4. Schedule the assignment to be available to all students at 8AM the following morning
#   5. Delete the old assignments and old copies of the drive files

# This script is... talkative.
        
import pickle
import argparse
import os.path
import sys
import datetime
import json

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

# The secrets file is used to store the course ID of my mother's google classroom course.
# It also stores a dictionary of all template file names mapped to their corresponding
# google drive file ID's. It is in a separate file, not stored on github because I would
# rather not expose my mother's classroom ID to the world. This file we have to be created
# manually on her computer.
import SECRETS

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

# The error file gets sent to me and contains any exception information captured. 
# As of right now, this should only occur as a part of the HTTP connection process
# if the google client refuses to build credentials or accept an HTTP request
ERROR_FILE = "send_me_to_kellen.json"


# the data directory holds files which my mother should not be interacting
# with in almost any capacity
DATA_DIRECTORY = "data"

# the yesterday file contains all created assignment ID's and copied drive file ID's
# this list is serialized as a list of tuples (assignment id, drive file ID)
YESTERDAY_FILE = os.path.join(DATA_DIRECTORY, "yesterday.pickle")

# so here is where things get a pit more interesting. the google API for both drive and 
# classroom uses OATH2 to authenticate HTTP requests. OATH2 is VERY strict about the 
# access it gives out. My mother's OATH2 credentials for google drive do not apply
# to her OATH2 credentials for classroom, so both need to be approved. Her credentials
# get serialized into two separate files: classroom_credential.json, 
# and drive_credentials.json each containing the corresponding credentials 
# to build an OATH2 service using her API secret key. The scopes (stored in the lists
# below), contains the privilege that this script is request (I will go through that
# in a bit). Together with the credentials file, the scopes are used to build the Token
# and this token gets passed to the google HTTP client in order to build a service

CLASSROOM_PICKLE = os.path.join(DATA_DIRECTORY, "classroom_token.pickle")
CLASSROOM_CREDENTIALS = os.path.join(DATA_DIRECTORY, "classroom_credentials.json")

# first scope allows me to modify student coursework. I am unable to read or modify anything
# outside of assignments using this scope. The second allows me to read (but not modify)
# courses. This is necessary as almost all google classroom API requests require a course 
# ID to be supplied. I am using this scope to get the ID's of my mothers courses to be
# used in later calls. This should only be run once, as my mother only has one course
# created and as far as I know has no need to create more.

# reference for class scopes: https://developers.google.com/classroom/guides/auth
CLASSROOM_SCOPES = ["https://www.googleapis.com/auth/classroom.coursework.students", 
          "https://www.googleapis.com/auth/classroom.courses.readonly"]
 
DRIVE_PICKLE = os.path.join(DATA_DIRECTORY, "drive_token.pickle")
DRIVE_CREDENTIALS = os.path.join(DATA_DIRECTORY, "drive_credentials.json")

# This scope is ... a bit disingenuously named. It does NOT give me full access to 
# my mothers drive files. Instead, it only gives me access to the drive files that 
# this app has created. The only files I will be created will be daily copies of 
# the template file

# reference for drive scopes: https://developers.google.com/drive/api/v2/about-auth
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# my mother wanted the assignments to appear to the students at 8 in the morning
CLASS_START = datetime.time(8, 00, 00)

# all this function does is grab whatever data was in the exception and write it to the error file
# my mother can then send me this file.
def record_exception(e):
    with open(ERROR_FILE, "wt") as error_file:
        json.dump(json.loads(e.content), error_file)
        print(f"Uh oh mom! I got an error. I wrote it down though. Will you email me: {ERROR_FILE}")


# here is where the credentials get built and stored
def build_credentials(pickle_file, scopes, creds_file):
    creds = None
    # if we have previously build the pickle file, then just use that, if not, we do things
    # the hard way and generate a new one
    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as token:
            creds = pickle.load(token)
            # these aren't *really* credentials, but my mother doesn't need to know that.
            print("I found your credentials! Hold on a minute, I'm gonna talk to google")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif os.path.exists(creds_file):
            # this will open up a browser so my mother can allow the script access to the API
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, scopes)
            creds = flow.run_local_server(port=0)

    if creds is None: 
        print("I can't find your credentials! I have no way to convince google that it's you! Will you call me?")
        return creds
    with open(pickle_file, 'wb') as token:
        # stored the token. This needs to be deleted if the scopes ever change
        pickle.dump(creds, token)
    return creds


def build_classroom_service():
    creds = build_credentials(CLASSROOM_PICKLE, CLASSROOM_SCOPES, CLASSROOM_CREDENTIALS)
    return None if creds is None else build('classroom', 'v1', credentials=creds)


def build_drive_service():
    creds = build_credentials(DRIVE_PICKLE, DRIVE_SCOPES, DRIVE_CREDENTIALS)
    return None if creds is None else build('drive', 'v3', credentials=creds)


def get_assignment_date():
    now = datetime.datetime.now()
    weekday = now.weekday()
    if weekday < 4 and CLASS_START < now.time():
        # we are preparing tomorrows class (execption: friday), add date and return that
        now += datetime.timedelta(days=1)
    elif 4 < weekday or weekday == 4 and CLASS_START < now.time():
        # this is the weekend, prepare monday's class
        now += datetime.timedelta(days=(1 + (6 - weekday)))
    return now.replace(hour=CLASS_START.hour, minute=CLASS_START.minute, second=CLASS_START.second)


def add_assignment(classroom_service, assignment_name, tomorrow_date, drivefile_id):
    assign_blob = { 
            "title": assignment_name,
            "materials" : [ { "driveFile": { "driveFile": { "id": drivefile_id, "title": assignment_name} } } ],
            "workType": "ASSIGNMENT",
            "state": "DRAFT",
            "assigneeMode": "ALL_STUDENTS",
            "scheduledTime": tomorrow_date.strftime("%Y-%m-%dT%H:%M:%S-04:00"),
            "associatedWithDeveloper": True }

    try:
        assign_result = classroom_service.courses().courseWork().create(courseId=SECRETS.COURSE_ID, 
                body=assign_blob).execute()
    except HttpError as error:
        record_exception(error)
        return 0
    return assign_result["id"]


def copy_drive_file(drive_service, daily_assignment, template_file_id):
    try:
        copy_result = drive_service.files().copy(fileId=template_file_id, body={"name":daily_assignment}).execute()
    except HttpError as error:
        record_exception(error)
        return 0
    return copy_result["id"]


def clean_yesterday(drive_service, class_service, id_list):
    for assignment_id, drivefile_id in id_list:
        class_service.courses().courseWork().delete(courseId=SECRETS.COURSE_ID, id=assignment_id).execute()
        drive_service.files().delete(fileId=drivefile_id).execute()


def perform_copy_and_create(drive_serv, class_serv):
    dt_tomorrow = get_assignment_date()
    day = dt_tomorrow.strftime("%A")
    for assignment_name, template_file_id in SECRETS.ASSIGNMENT_DICT:
        daily_assignment = assignment_name + " - " + day
        drivefile_id = copy_drive_file(drive_serv, daily_assignment, template_file_id)
        if 0 == drivefile_id:
            return

        print(f"I just copied {assignment_name} to {daily_assignment}! Now I will make the assignment!")
        assignment_id = add_assignment(class_serv, daily_assignment, dt_tomorrow, drivefile_id)
        if 0 == assignment_id:
            return

        print(f"I just created your assignment: {daily_assignment}!")
        yield assignment_id, drivefile_id


def prepare_tomorrow():
    class_serv = build_classroom_service()
    drive_serv = build_drive_service()
    if drive_serv is None or class_serv is None:
        return False
    if os.path.exists(YESTERDAY_FILE):
        print("I'm going to get tomorrow set up, first I have to delete the old assignments")
        with open(YESTERDAY_FILE, "rb") as yesterday:
            clean_yesterday(drive_serv, class_serv, pickle.load(yesterday))
        print("Ok, I got those out of the way, now I'm gonna make the daily assignments")

    created_ids = [id_pair for id_pair in perform_copy_and_create(drive_serv, class_serv)]
    print("Ok, I have added all the assignments! Now I'm gonna write some stuff down so I remember what I created")
    with open(YESTERDAY_FILE, "wb") as tomorrow_file:
        pickle.dump(created_ids, tomorrow_file)
    print("All your classes are created and you are good to go! Good luck mom!")
    return True
    

def list_course_ids():
    service = build_classroom_service()
    if service is None:
        print("Uh oh, I was unable to talk to google, so I can't get a list of the course IDs")
        return False
    try:
        course_response = service.courses().list().execute()
    except HttpError as e:
        record_exception(e)
        return False
    else:
        print("I got the course list! I'm going to list them below!")
        for course in course_response['courses']:
            course_name = course['name']
            course_id = course['id']
            print(f"{course_name}: {course_id}")
    print("Alright mom! I'm done here, find the course ID for the class you need and add it to the config file!")
    return True


def main():

    print("Hi mom! I'm gonna help you get your classroom set up!")
    if not os.path.exists(DATA_DIRECTORY): 
        os.mkdir(DATA_DIRECTORY)

    parser = argparse.ArgumentParser()
    # this command line option is used to get the course ID for my mother's classes. As far as I know
    # there is no other way to get this ID, so it has to be done manually, before running
    # the normal routine
    parser.add_argument('--courses', action='store_true', help='Lists all course IDs for your classes!')
    args = parser.parse_args()
    script_result = EXIT_SUCCESS
    if args.courses:
        print("I'm going to take a peek at your google classroom and get a list of all the course IDs!")
        script_result = EXIT_SUCCESS if list_course_ids() else EXIT_FAILURE

    else:
        print("I'm going to prepare your class for tomorrow!")
        script_result = EXIT_SUCCESS if prepare_tomorrow() else EXIT_FAILURE

    if EXIT_SUCCESS == script_result:
        print("Alright mom! I am done for now! Love you!")
    else:
        print("Something went wrong! I couldn't get everything set up. Will you text or call me?")

    return script_result 


if __name__ == '__main__':
    sys.exit(main())
