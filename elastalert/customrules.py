# -*- coding: utf-8 -*-
from ruletypes import *
from collections import OrderedDict
import json
import unicodedata
import pytz

class CardinalityRule(RuleType):
    """ A rule that matches if max_cardinality of a field is reached within a timeframe """
    required_options = frozenset(['cardinality_term','max_cardinality', 'timeframe'])

    def __init__(self, *args):
        super(CardinalityRule, self).__init__(*args)
        self.storage = self.rules.get('storage', '/tmp/dldm/elastalertConfig/tmp/cardinalityWindow.json')
        self.ts_field = self.rules.get('time_field', 'time')
        self.get_ts = lambda event: ts_to_dt(event[self.ts_field])
        self.cardinalityWindow = jsonRecordInterface(self.storage).load()
        self.cterm = self.rules['cardinality_term']

    def add_data(self, data):
        if data:
            if 'bucket_key' in self.rules:
                bk = self.rules['bucket_key']
            else:
                bk = None

            for event in data:
                if (bk and (bk in event.keys())) or (not bk):
                    if (self.cterm in event.keys()):
                        if bk:
                            key = unicodedata.normalize('NFKD', hashable(lookup_es_key(event, bk))).encode('ascii','ignore')
                        else:
                            # If no bucket_key, we use the key 'all' for all events
                            key = 'all'
                        keyRecord = self.cardinalityWindow.content.setdefault(key,[])
                        item = {'alerted':'','term':event[self.cterm],'time':self.get_ts(event)}
                        append = True
                        for record in keyRecord:
                            if record['term']==item['term'] and record['time']==item['time']:
                                append = False
                        if append:
                            keyRecord.append(item)
            for keyitem in self.cardinalityWindow.content.keys():
                self.check_for_match(keyitem)
            self.cardinalityWindow.write()

    def check_for_match(self, key):
        # Match if, after removing old events, we hit max_cardinality
        silentList = []
        ctermList = []
        for ele in self.cardinalityWindow.content[key]:
            if ele['alerted'] and (ts_now()-ele['alerted'] < self.rules['timeframe']):
                silentList.append(ele['term'])
        for ele in self.cardinalityWindow.content[key]:
            if ele['term'] not in silentList:
                if not ele['alerted']:
                    ctermList.append(ele['term'])
        ctermSet = set(ctermList)
        if len(ctermSet) >= self.rules['max_cardinality']:
            timenow = ts_now()
            match = {}
            if self.rules['bucket_key']:
                match[self.rules['bucket_key']]=key
                match['cardinality']=len(ctermSet)
                match['time']=timenow
            else: 
                match['cardinality']=len(ctermSet)
                match['time']=timenow
            self.add_match(match)
            for cardinality in ctermSet:
                self.tagAlerted(key,cardinality, timenow)
                
    def add_match(self, match):
        """ This function is called on all matching events. Rules use it to add
        extra information about the context of a match. Event is a dictionary
        containing terms directly from elasticsearch and alerts will report
        all of the information.

        :param event: The matching event, a dictionary of terms.
        """
        # Convert datetime's back to timestamps
        if 'time' in match.keys():
            match['time'] = utc_to_local(match['time'])
        self.matches.append(match)

    def tagAlerted(self,key,cardinality, timenow):
        for ele in self.cardinalityWindow.content[key]:
            if ele['term']==cardinality:
                if not ele['alerted']:
                    ele['alerted'] = timenow

    def garbage_collect(self, timestamp):
        """ Remove all occurrence data that is beyond the timeframe away """
        stallKeys = []
        for key in self.cardinalityWindow.content.keys():
            self.cardinalityWindow.content[key] = [item for item in self.cardinalityWindow.content[key] if ((timestamp - item['time'] < self.rules['timeframe']) or (item['alerted'] and (timestamp - item['alerted'] < self.rules['timeframe'])))]
            if not self.cardinalityWindow.content[key]:
                stallKeys.append(key)
        for key in stallKeys:
            del self.cardinalityWindow.content[key]
        self.cardinalityWindow.write()

    def get_match_str(self, match):
        lt = True
        starttime = pretty_dl_ts(ts_now() - self.rules['timeframe'], lt)
        endtime = pretty_dl_ts(ts_now(), lt)
        if not self.rules['bucket_key']:
            message = 'A maximum cardinality of %d on %s has been reached since the last alert or between %s and %s\n\n' % (self.rules['max_cardinality'],self.rules['cardinality_term'],
                                                                         starttime,
                                                                         endtime)
        else:
            message = 'A maximum cardinality of %d on %s has been reached for %s: %s since the last alert or between %s and %s\n\n' % (self.rules['max_cardinality'],self.rules['cardinality_term'],\
                                                                         self.rules['bucket_key'],
                                                                         match[self.rules['bucket_key']],
                                                                         starttime,
                                                                         endtime)
        return message


class PeriodicReporter(RuleType):
    """ A rule that generates a report every timeframe """
    required_options = frozenset(['timeframe'])

    def __init__(self, *args):
        super(PeriodicReporter, self).__init__(*args)
        self.storage = self.rules.get('storage', '/tmp/dldm/elastalertConfig/tmp/report.json')
        self.ts_field = self.rules.get('time_field', 'time')
        self.get_ts = lambda event: ts_to_dt(event[self.ts_field])
        self.recordWindow = jsonReportInterface(self.storage).load()
        self.recordWindow.content.setdefault('report_start',dt_to_ts(ts_now()))
        self.recordWindow.content.setdefault('last_report','')

    def add_data(self, data):
        for event in data:
            if 'msg' in event.keys():
                if str(event['msg'])=='Successful user registration':
                    new_user = {}
                    new_user['User']=event['user']
                    new_user['Registration Time']=dt_to_ts(self.get_ts(event))
                    new_user['Reported']=''
                    self.recordWindow.content.setdefault('New User Registration(s)',[])
                    append = True
                    for ele in self.recordWindow.content['New User Registration(s)']:
                        if ele['Registration Time']==new_user['Registration Time']:
                            append = False
                    if append:
                        self.recordWindow.content['New User Registration(s)'].append(new_user)
                elif str(event['msg'])=='Successful user pre-registration':
                    new_user = {}
                    new_user['User']=event['user']
                    new_user['Registration Time']=dt_to_ts(self.get_ts(event))
                    new_user['Reported']=''
                    self.recordWindow.content.setdefault('New User Registration(s) by Admin',[])
                    append = True
                    for ele in self.recordWindow.content['New User Registration(s) by Admin']:
                        if ele['Registration Time']==new_user['Registration Time']:
                            append = False
                    if append:
                        self.recordWindow.content['New User Registration(s) by Admin'].append(new_user)
                elif str(event['msg'])=='Enqueued invite to org email':
                    new_user = {}
                    new_user['User']=event['user']
                    new_user['Registration Time']=dt_to_ts(self.get_ts(event))
                    new_user['Reported']=''
                    self.recordWindow.content.setdefault('New User Invited by Existing User',[])
                    append = True
                    for ele in self.recordWindow.content['New User Invited by Existing User']:
                        if ele['User']==new_user['User']:
                            append = False
                    if append:
                        self.recordWindow.content['New User Invited by Existing User'].append(new_user)
                elif str(event['msg'])=='Entitlement payment complete. Enqueued payment receipt email to purchaser':
                    new_user = {}
                    new_user['User']=event['purchased_by']
                    new_user['Subscription Time']=dt_to_ts(self.get_ts(event))
                    new_user['Subscription Package']=event.setdefault('item','NOT SPECIFIED')
                    new_user['Reported']=''
                    self.recordWindow.content.setdefault('New Subscription(s)',[])
                    append = True
                    for ele in self.recordWindow.content['New Subscription(s)']:
                        if ele['Subscription Time']==new_user['Subscription Time']:
                            append = False
                    if append:
                        self.recordWindow.content['New Subscription(s)'].append(new_user)
        self.check_for_match()
        self.recordWindow.write()

    def stripDomain(self,stringinput):
        item = str(stringinput)
        item = item[:item.find('@')]
        return item

    def check_for_match(self):
        # Match if, after removing old events, we hit max_cardinality
        tsnow = ts_now()
        if (tsnow>ts_to_dt(self.recordWindow.content['report_start'])) and (tsnow-ts_to_dt(self.recordWindow.content['report_start'])>self.rules['timeframe']):
            report = OrderedDict()
            if 'New User Registration(s)' in self.recordWindow.content.keys():
                for ele in self.recordWindow.content['New User Registration(s)']:
                    if not ele['Reported']:
                        item = OrderedDict()
                        item['User'] = ele['User']
                        item['Registration Time'] = utc_to_local(ts_to_dt(ele['Registration Time']))
                        report.setdefault('New User Registration(s)',[]).append(item)
            if 'New User Registration(s) by Admin' in self.recordWindow.content.keys():
                for ele in self.recordWindow.content['New User Registration(s) by Admin']:
                    if not ele['Reported']:
                        item = OrderedDict()
                        item['User'] = ele['User']
                        item['Registration Time'] = utc_to_local(ts_to_dt(ele['Registration Time']))
                        report.setdefault('New User Registration(s) by Admin',[]).append(item)
            if 'New User Invited by Existing User' in self.recordWindow.content.keys():
                for ele in self.recordWindow.content['New User Invited by Existing User']:
                    if not ele['Reported']:
                        item = OrderedDict()
                        item['User'] = ele['User']
                        item['Registration Time'] = utc_to_local(ts_to_dt(ele['Registration Time']))
                        report.setdefault('New User Invited by Existing User',[]).append(item)
            if 'New Subscription(s)' in self.recordWindow.content.keys():
                for ele in self.recordWindow.content['New Subscription(s)']:
                    if not ele['Reported']:
                        item = OrderedDict()
                        item['User'] = ele['User']
                        item['Subscription Time'] = utc_to_local(ts_to_dt(ele['Subscription Time']))
                        item['Subscription Package'] = ele['Subscription Package']
                        report.setdefault('New Subscription(s)',[]).append(item)
            if not report:
                report['Message']='Nothing worth reporting happened. Dragon is quite starving, new blood please!'
            if self.recordWindow.content['last_report']:
                report['Start Time']=self.recordWindow.content['last_report']
            else:
                report['Start Time']=self.recordWindow.content['report_start']
            while (tsnow>ts_to_dt(self.recordWindow.content['report_start'])+self.rules['timeframe']) and (tsnow-ts_to_dt(self.recordWindow.content['report_start'])>self.rules['timeframe']):
                self.recordWindow.content['report_start']=dt_to_ts(ts_to_dt(self.recordWindow.content['report_start'])+self.rules['timeframe'])
            report['End Time']=dt_to_ts(tsnow)
            self.add_match(report)
            self.recordWindow.content['last_report']=dt_to_ts(tsnow)
            self.tagAlerted()

    def add_match(self, match):
        """ This function is called on all matching events. Rules use it to add
        extra information about the context of a match. Event is a dictionary
        containing terms directly from elasticsearch and alerts will report
        all of the information.

        :param event: The matching event, a dictionary of terms.
        """
        # Convert datetime's back to timestamps
        self.matches.append(match)

    def tagAlerted(self):
        for key in self.recordWindow.content.keys():
            if key!= 'report_start' and key!= 'last_report':
                for ele in self.recordWindow.content[key]:
                    if not ele['Reported']:
                        ele['Reported']=self.recordWindow.content['last_report']      

    def garbage_collect(self, timestamp):
        """ Remove all occurrence data that is beyond the timeframe away """
        for key in self.recordWindow.content.keys():
            if key!='report_start' and key!='last_report':
                stallEle = []
                for ele in self.recordWindow.content[key]:
                    if ele['Reported']:
                        if ts_to_dt(ele['Reported'])+2*self.rules['timeframe']<ts_to_dt(self.recordWindow.content['report_start']):
                            stallEle.append(ele)
                for element in stallEle:
                    self.recordWindow.content[key].remove(element)
        self.recordWindow.write()

    def get_match_str(self, match):
        lt = True
        starttime = pretty_dl_ts(match['Start Time'], lt)
        endtime = pretty_dl_ts(match['End Time'], lt)
        del match['Start Time']
        del match['End Time']
        message = 'Reporting Period: [%s, %s]\n\n' % (starttime,endtime)
       
        return message


def jsonLoad(src):
    try:
        with open(src,'r') as f:
            content=json.load(f)
    except:
        content={}
    return content

class jsonRecordInterface():
    def __init__(self,infoSrc):
        self.infoSrc = infoSrc
        self.content = {}
        
    def load(self):
        self.content=jsonLoad(self.infoSrc)
        for key in self.content.keys():
            for event in self.content[key]:
                event['time']=ts_to_dt(event['time'])
                if event['alerted']:
                    event['alerted']=ts_to_dt(event['alerted'])
        return self
            
    def extend(self,content):
        self.content.update(content)
        return self
        
    def write(self):
        try:
            writable = {}
            if self.content:
                for key in self.content.keys():
                    writable[key] = []
                    for event in self.content[key]:
                        if event['alerted']:
                            writable[key].append({'alerted':dt_to_ts(event['alerted']),'term':event['term'],'time':dt_to_ts(event['time'])})
                        else:
                            writable[key].append({'alerted':'','term':event['term'],'time':dt_to_ts(event['time'])})
            with open(self.infoSrc,'w+') as f:
                json.dump(writable,f,ensure_ascii=False,indent=4,separators=(',',':'))
        except OSError as e:
            sys.stdout.write('Error writing to '+self.infoSrc+': '+str(e)+'\n')

class jsonReportInterface():
    def __init__(self,infoSrc):
        self.infoSrc = infoSrc
        self.content = {}
        
    def load(self):
        self.content=jsonLoad(self.infoSrc)
        return self
            
    def extend(self,content):
        self.content.update(content)
        return self
        
    def write(self):
        try:
            with open(self.infoSrc,'w+') as f:
                json.dump(self.content,f,ensure_ascii=False,indent=4,separators=(',',':'))
        except OSError as e:
            sys.stdout.write('Error writing to '+self.infoSrc+': '+str(e)+'\n')
        

## You could use `tzlocal` module to get local timezone on Unix and Win32
# from tzlocal import get_localzone # $ pip install tzlocal

# # get local timezone    
# local_tz = get_localzone()

def utc_to_local(utc_dt):
    local_tz = pytz.timezone('Asia/Hong_Kong')
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
    return local_tz.normalize(local_dt).strftime('%Y-%m-%dT%H:%M:%S.%f%z')

def pretty_dl_ts(utctimestamp, tz=True):
    """Pretty-format the given timestamp (to be printed or logged hereafter).
    If tz, the timestamp will be converted to local time.
    Format: MM-DD HH:MM TZ"""
    dt = utctimestamp
    if not isinstance(dt, datetime.datetime):
        dt = ts_to_dt(dt)
    if tz:
        dt = dt.replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Hong_Kong'))
    padding = ''
    if dt.minute < 10:
        padding = '0'
    return "'%d-%d-%d %d:%s%d %s'" % (dt.year, dt.month, dt.day,
                                 dt.hour, padding, dt.minute, dt.tzname())