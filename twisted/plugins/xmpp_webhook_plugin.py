# -*- coding: utf-8 -*-
'''
Created on 01-Dec-2014

@author: 3cky
'''

import os

from zope.interface import implements

from twisted.python import usage
from twisted.plugin import IPlugin
from twisted.application.service import IServiceMaker
from twisted.web.resource import Resource
from twisted.web import server
from twisted.application import internet, service
from twisted.words.protocols.jabber.jid import JID

from wokkel.client import XMPPClient
from ConfigParser import ConfigParser
from jinja2 import Environment, PackageLoader, FileSystemLoader

from xmpp_webhook.handlers import MUCHandler, WebHookHandler

DEFAULT_HTTP_PORT = 8080
DEFAULT_NICKNAME = 'git-commits'
DEFAULT_TEMPLATE_NAME = 'message.txt'

class Options(usage.Options):
    optParameters = [["config", "c", None, 'Configuration file name']]


class ServiceManager(object):
    implements(IServiceMaker, IPlugin)
    tapname = "gitlab-webhook-xmpp"
    description = "GitLab push event XMPP notification web hook."
    options = Options
    mucHandlers = []

    def makeService(self, options):
        """
        Make services to handle push event notifications.
        """
        # check confguration file is specified and exists
        if not options["config"]:
            raise ValueError('Configuration file not specified (try to check --help option)')
        cfgFileName = options["config"];
        if not os.path.isfile(cfgFileName):
            raise ValueError('Configuration file not found:', cfgFileName)

        # read configuration file
        cfg = ConfigParser()
        cfg.read(cfgFileName)

        # create Twisted application
        application = service.Application("gitlab-webhook-xmpp")
        serviceCollection = service.IServiceCollection(application)

        # create XMPP client
        client = XMPPClient(JID(cfg.get('xmpp', 'jid')), cfg.get('xmpp', 'password'))
#         client.logTraffic = True
        client.setServiceParent(application)
        # join to all MUC rooms
        nickname = cfg.get('xmpp', 'nickname') if cfg.has_option('xmpp', 'nickname') else DEFAULT_NICKNAME
        notifications = cfg.items('notifications')
        for room, repositoryMasks in notifications:
            mucHandler = MUCHandler(JID(room), nickname, repositoryMasks.split(','))
            mucHandler.setHandlerParent(client)
            self.mucHandlers.append(mucHandler)

        templateLoader = None
        if cfg.has_option('message', 'template'):
            templateFullName = cfg.get('message', 'template')
            templatePath, self.templateName = os.path.split(templateFullName)
            templateLoader = FileSystemLoader(templatePath)
        else:
            self.templateName = DEFAULT_TEMPLATE_NAME
            templateLoader = PackageLoader('xmpp_webhook', 'templates')
        self.templateEnvironment = Environment(loader=templateLoader, extensions=['jinja2.ext.i18n'])
        self.templateEnvironment.install_null_translations() # use i18n to pluralize only

        # create web hook handler
        rootHttpResource = Resource()
        rootHttpResource.putChild('', WebHookHandler(self))
        site = server.Site(rootHttpResource)
        httpPort = cfg.getint('http', 'port') if cfg.has_option('http', 'port') else DEFAULT_HTTP_PORT
        httpServer = internet.TCPServer(httpPort, site)
        httpServer.setServiceParent(serviceCollection)

        return serviceCollection

    def notifyPush(self, pushData):
        """
        Send message with push data to all XMPP handlers matching repository URL
        """
        repositoryUrl = pushData.get('repository').get('url')
        kind = pushData.get('object_kind')
        if kind == 'push':
            template_name = 'message_push.txt'
        elif kind == 'issue':
            template_name = 'message_issue.txt'
        elif kind == 'merge_request':
            template_name = 'message_mr.txt'
        elif kind == 'note':
            type_ = pushData.get('object_attributes').get('noteable_type')
            if type_ == 'Issue':
                template_name = 'message_note_issue.txt'
            elif type_ == 'MergeRequest':
                template_name = 'message_note_mr.txt'
            elif type_ == 'Commit':
                template_name = 'message_note_commit.txt'
            else:
                return
        else:
            return
        template = self.templateEnvironment.get_template(template_name)
        for mucHandler in self.mucHandlers:
            if mucHandler.matchRepositoryMask(repositoryUrl):
                mucHandler.sendMessage(template.render(push=pushData))


serviceManager = ServiceManager()
