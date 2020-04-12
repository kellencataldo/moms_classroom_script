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

import SECRETS


EXIT_SUCCESS = 0
EXIT_FAILURE = 1


JSON_ASSIGNMENT_KEY = "assignments"
JSON_DRIVE_KEY = "drivefiles"
DATA_DIRECTORY = "data"

ERROR_FILE = "send_me_to_kellen.json"
YESTERDAY_FILE = os.path.join(DATA_DIRECTORY, "yesterday.json")

CLASSROOM_PICKLE = os.path.join(DATA_DIRECTORY, "classroom_token.pickle")
CLASSROOM_CREDENTIALS = os.path.join(DATA_DIRECTORY, "classroom_credentials.json")
CLASSROOM_SCOPES = ["https://www.googleapis.com/auth/classroom.coursework.students", 
          "https://www.googleapis.com/auth/classroom.courses.readonly"]
 
DRIVE_PICKLE = os.path.join(DATA_DIRECTORY, "drive_token.pickle")
DRIVE_CREDENTIALS = os.path.join(DATA_DIRECTORY, "drive_credentials.json")
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

CLASS_START = datetime.time(8, 00, 00)


def record_exception(e):
    with open(ERROR_FILE, "wt") as error_file:
        json.dump(json.loads(e.content), error_file)
        print(f"Uh oh mom! I got an error. I wrote it down though. Will you email me: {ERROR_FILE}")


def build_credentials(pickle_file, scopes, creds_file):
    creds = None
    if os.path.exists(pickle_file):
        with open(pickle_file, 'rb') as token:
            creds = pickle.load(token)
            print("I found your credentials! Hold on a minute, I'm gonna talk to google")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif os.path.exists(creds_file):
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, scopes)
            creds = flow.run_local_server(port=0)

    if creds is None: 
        print("I can't find your credentials! I have no way to convince google that it's you! Will you call me?")
        return creds

    with open(pickle_file, 'wb') as token:
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
    print(courseWork1)


def copy_drive_file(drive_service, daily_assignment, template_file_id):
    try:
        copy_result = drive_service.files().copy(fileId=template_file_id, body={"name":daily_assignment}).execute()
    except HttpError as error:
        record_exception(error)
        return 0
    return copy_result["id"]


def clean_yesterday(drive_service, class_service, json_object):
    for assignment_id in json_object[JSON_ASSIGNMENT_KEY]:
        class_service.courses().courseWork().create(courseId=SECRETS.COURSE_ID, id=assignment_id).execute()

    for drivefile_id in json_object[JSON_DRIVE_KEY]:
        drive_service.files().delete(fileId=drivefile_id).execute()


def prepare_tomorrow():

    classroom_service = build_classroom_service()
    if classroom_service is None:
        return False

    drive_service = build_drive_service()
    if drive_service is None:
        return False

    if os.path.exists(YESTERDAY_FILE) and False == clean_yesterday(drive_service, classroom_service):
        print("I'm going to get tomorrow set up, first I have to delete the old assignments")
        with open(YESTERDAY_FILE) as yesterday:
            created_files = json.load(yesterday)
            clean_yesterday(drive_service, class_service, created_files)
        print("Ok, I got those out of the way, now I'm gonna make the daily assignments")

    results_json = { JSON_ASSIGNMENT_KEY: [ ], JSON_DRIVE_KEY: [ ] }
    tomorrow = get_assignment_date()

    for assignment_pair in SECRETS.ASSIGNMENT_DICT:
        assignment_name, template_file_id = assignment_pair
        daily_assignment = assignment_name + " - " + tomorrow.strftime("%A")
        drivefile_id = copy_drive_file(drive_service, daily_assignment, template_file_id)
        if 0 == drivefile_id:
            clean_yesterday(drive_service, classroom_service, results_json)
            return False

        print(f"I just copied {assignment_name} to {daily_assignment}! Now I will make the assignment!")
        results_json[JSON_DRIVE_KEY].append(drivefile_id)
        assignment_id = add_assignment(classroom_service, assignment_name, tomorrow, drivefile_id)
        if 0 == assignment_id:
            clean_yesterday(drive_service, classroom_service, results_json)
            return False

        print(f"I just created your assignment: {daily_assignment}!")
        results_json[JSON_ASSIGNMENT_KEY].append(assignment_id)

    print("Ok, I have added all the assignments! Now I'm gonna write some stuff down so I remember what I created")
    with open(YESTERDAY_FILE, "wt") as tomorrow_file:
        json.dump(results_json, tomorrow_file)
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
