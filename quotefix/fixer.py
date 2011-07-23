from    AppKit          import *
from    quotefix.utils  import swizzle
from    quotefix.alert  import Alert
from    objc            import Category, lookUpClass
import  logging, re

# message types
REPLY       = 1
REPLY_ALL   = 2
FORWARD     = 3
DRAFT       = 4
NEW         = 5
SUPPORTED   = [ REPLY, REPLY_ALL, FORWARD ]

# our own MailDocumentEditor implementation
MailDocumentEditor = lookUpClass('MailDocumentEditor')
class MailDocumentEditor(Category(MailDocumentEditor)):

    @classmethod
    def __init__(cls, app):
        cls.app = app

    @swizzle(MailDocumentEditor, 'finishLoadingEditor')
    def finishLoadingEditor(self, original):
        logging.debug('MailDocumentEditor finishLoadingEditor')

        # execute original finishLoadingEditor()
        original(self)

        try:
            # check if we can proceed
            if not self.app.is_active:
                logging.debug("QuoteFix is not active, so no QuoteFixing for you!")
                return

            # check for supported messagetype
            logging.debug('message type is %s' % self.messageType())
            if self.messageType() not in SUPPORTED:
                logging.debug('\t not in %s, bailing' % SUPPORTED)
                return

            # grab composeView instance (this is the WebView which contains the
            # message editor) and check for the right conditions
            try:
                view = objc.getInstanceVariable(self, 'composeWebView')
            except:
                # was renamed in Lion
                view = objc.getInstanceVariable(self, '_composeWebView')

            # move cursor to end of document
            view.moveToEndOfDocument_(self)

            # grab some other variables we need to perform our business
            backend     = self.backEnd()
            htmldom     = view.mainFrame().DOMDocument()
            htmlroot    = htmldom.documentElement()

            # send original HTML to menu for debugging
            self.app.html = htmlroot.innerHTML()

            # remove quotes?
            if self.app.remove_quotes:
                logging.debug('calling remove_quotes()')
                self.remove_quotes(htmldom, self.app.remove_quotes_level)
                backend.setHasChanges_(False)

            # remove signature from sender
            logging.debug('calling remove_old_signature()')
            if self.remove_old_signature(htmldom, view):
                backend.setHasChanges_(False)

            # place cursor above own signature (if any)
            logging.debug('calling move_above_new_signature()')
            if self.move_above_new_signature(htmldom, view):
                backend.setHasChanges_(False)
            else:
                view.insertNewline_(self)

            # perform some general cleanups
            logging.debug('calling cleanup_layout()')
            if self.cleanup_layout(htmlroot):
                backend.setHasChanges_(False)

            # provide custom attribution?
            if self.app.use_custom_reply_attribution and self.messageType() in [ REPLY, REPLY_ALL ]:
                logging.debug("calling customize_attribution() for reply(-all)")
                self.customize_attribution(
                    dom         = htmldom,
                    view        = view,
                    reply       = backend.message(),
                    inreplyto   = backend.originalMessage(),
                    is_forward  = False
                )
            elif self.app.use_custom_forwarding_attribution and self.messageType() == FORWARD:
                logging.debug("calling customize_attribution() for forwarding")
                self.customize_attribution(
                    dom         = htmldom,
                    view        = view,
                    reply       = backend.message(),
                    inreplyto   = backend.originalMessage(),
                    is_forward  = True
                )

            # move to beginning of line
            logging.debug('calling view.moveToBeginningOfLine()')
            view.moveToBeginningOfLine_(self)

            # done
            logging.debug('QuoteFixing done')
        except Exception, e:
            logging.exception(e)
            if self.app.is_debugging:
                Alert.showException(self)

    def remove_quotes(self, dom, level):
        # find all blockquotes
        blockquotes = dom.querySelectorAll_("blockquote")
        for i in range(blockquotes.length()):
            blockquote = blockquotes.item_(i)
            # check quotelevel against maximum allowed level
            if blockquote.quoteLevel() >= level:
                blockquote.parentNode().removeChild_(blockquote)

    # try to find, and remove, signature of sender
    SIGSEP = re.compile(r'--(?:&nbsp;| |\xa0)|^--$', re.M|re.S|re.I)
    def remove_old_signature(self, dom, view):
        signature   = None
        root        = dom.documentElement()

        # grab first blockquote (if any)
        blockquote = root.firstDescendantBlockQuote()
        if not blockquote:
            return False

        # find nodes which might contain senders signature
        possibles = [
            #"body > div > blockquote > div > br",
            "body > div > blockquote br",
            "body > blockquote br",
            "body > blockquote > div",
        ]

        nodes = []
        for possible in possibles:
            matches = dom.querySelectorAll_(possible)
            nodes += [ matches.item_(i) for i in range(matches.length()) ]

        # try to find a signature
        for node in nodes:
            # skip nodes which aren't at quotelevel 1
            if node.quoteLevel() != 1:
                continue

            # BR's are empty, so treat them differently
            if node.nodeName().lower() == 'br':
                next = node.nextSibling()
                if isinstance(next, DOMText) and self.SIGSEP.match(next.data()):
                    signature = node
                    break
            elif node.nodeName().lower() == 'div' and self.SIGSEP.match(node.innerHTML()):
                signature = node
                break

        # if we found a signature, remove it
        if signature:
            # remove all siblings following signature, except for attachments
            parent  = signature.parentNode()
            node    = signature.nextSibling()
            while node:
                if node.nodeName().lower() == 'object':
                    node = node.nextSibling()
                else:
                    next = node.nextSibling()
                    parent.removeChild_(node)
                    node = next

            # move down a line
            view.moveDown_(self)

            # and insert a paragraph break
            view.insertParagraphSeparator_(self)

            # remove empty lines
            blockquote.removeStrayLinefeeds()

            # signal that we removed an old signature
            return True

        # found nothing?
        return False

    def move_above_new_signature(self, dom, view):
        # find new signature by ID
        div = dom.getElementById_("AppleMailSignature")
        if not div:
            return False

        # set selection range
        domrange = dom.createRange()
        domrange.selectNode_(div)

        # create selection
        view.setSelectedDOMRange_affinity_(domrange, 0)

        # move up (positions cursor above signature)
        view.moveUp_(self)

        # and insert a paragraph break
        view.insertParagraphSeparator_(self)

        # signal that we moved
        return True

    def cleanup_layout(self, root):
        # clean up stray linefeeds
        root.getElementsByTagName_("body").item_(0)._removeStrayLinefeedsAtBeginning()

        # remove trailing whitespace on first blockquote?
        if self.app.remove_trailing_whitespace:
            blockquote = root.firstDescendantBlockQuote()
            if blockquote:
                blockquote._removeStrayLinefeedsAtEnd()

        # done?
        if self.app.keep_attribution_whitespace:
            return True

        # clean up linebreaks before first blockquote
        blockquote = root.firstDescendantBlockQuote()
        if blockquote:
            parent  = blockquote.parentNode()
            node    = blockquote.previousSibling()
            while node and node.nodeName().lower() == 'br':
                parent.removeChild_(node)
                node = blockquote.previousSibling()

        return True

    # provide customize attribution
    def customize_attribution(self, dom, view, reply, inreplyto, is_forward):
        if is_forward:
            matcher = re.compile(reply.original_forwarding_attribution)
        else:
            matcher = re.compile(reply.original_reply_attribution)

        # find parent of first quote
        root = dom.documentElement()
        node = root.firstDescendantBlockQuote().parentNode()
        if not node:
            return False

        # check children for attribution node
        children = node.childNodes()
        for i in range(children.length()):
            child = children.item_(i)
            if child.nodeType() == 1 and not matcher.match(child.innerHTML()):
                continue
            elif child.nodeType() == 3 and not matcher.match(child.data()):
                continue

            # render attribution
            attribution = reply.render_attribution(
                inreplyto   = inreplyto,
                is_forward  = is_forward,
            )

            # encode entities
            attribution = attribution.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            # replace newlines with hard linebreaks
            attribution = attribution.replace('\n', '<br/>')

            # replace old attribution with new
            if child.nodeType() == 1:
                child.setInnerHTML_(attribution)
            else:
                newnode = dom.createElement_("span")
                newnode.setInnerHTML_(attribution)
                node.replaceChild_oldChild_(newnode, child)

            # done
            return True

        # done nothing
        return False
