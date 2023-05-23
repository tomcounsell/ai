from settings import OPENAI_API_KEY
import openai

# Load your API key from an environment variable or secret management service
openai.api_key = OPENAI_API_KEY
response = openai.Completion.create(engine="davinci", prompt="This is a test", max_tokens=5)

that_thing = """
Decentralization
By storing data across its peer-to-peer network, the blockchain eliminates a number of risks that come with data being held centrally.[3] The decentralized blockchain may use ad hoc message passing and distributed networking. One risk of a lack of a decentralization is a so-called "51% attack" where a central entity can gain control of more than half of a network and can manipulate that specific blockchain record at will, allowing double-spending.[33]
Peer-to-peer blockchain networks lack centralized points of vulnerability that computer crackers can exploit; likewise, it has no central point of failure. Blockchain security methods include the use of public-key cryptography.[34]:5 A public key (a long, random-looking string of numbers) is an address on the blockchain. Value tokens sent across the network are recorded as belonging to that address. A private key is like a password that gives its owner access to their digital assets or the means to otherwise interact with the various capabilities that blockchains now support. Data stored on the blockchain is generally considered incorruptible.[3]
Every node in a decentralized system has a copy of the blockchain. Data quality is maintained by massive database replication[35] and computational trust. No centralized "official" copy exists and no user is "trusted" more than any other.[34] Transactions are broadcast to the network using software. Messages are delivered on a best-effort basis. Mining nodes validate transactions,[23] add them to the block they are building, and then broadcast the completed block to other nodes.[25]:ch. 08 Blockchains use various time-stamping schemes, such as proof-of-work, to serialize changes.[36] Alternative consensus methods include proof-of-stake.[23] Growth of a decentralized blockchain is accompanied by the risk of centralization because the computer resources required to process larger amounts of data become more expensive.[37]

I rephrased it for her, in plain language so my 5-year-old niece can understand:
"""

"""
\nCryptocurrency is a type of math money grown from computers. 
The computers make lots of math problems to create more computers, which make more math problems to create even more computers. 
The goal is to make a universal worldwide money to make buying and selling things online easier. 
Instead of a store or bank that holds your money, a private key is a line of code that gives a specific person access to their money. 
To buy things for this special money, you need to join a special club by putting your name and these private keys together in a wallet program. 
Because there's no bank, people who use the club can send and receive the money any time they want. 
There's no need to raid a bank, stick up a store, or otherwise hurt people. 
Everyone trusts everyone else to treat the money fairly. 
If someone does cheat, people can use other people to catch the cheater and keep the computers growing. 
If just a few people try to cheat and no one catches them, these few computers will stop growing and pile up in the basement. 
As the pile of computers gets deeper and deeper, the cheaters will be able to get more and more money while everyone else gets less and less. 
This is what happened in 2008 when many banks were getting cheated and all the money piled up in the basement. 
That's why the banks seem to get richer and richer even though most people get poorer and poorer.
 I'm afraid that if we don't fix this problem soon, the banks will get too powerful and ruin the club. 
 Monereo banks and governments and other types of approved clubs and apps and services and the people and businesses and 
 countries and cultures and languages and histories and stuff we love and hold dear and everyone we care about. 
 What we really need is a way to fix the computers and get them growing again without having to use all the buildings in our 
 city or drag us into a dangerous battle. Maybe building the next computer \"block\" into the chain with something 
 that's already there can tell us when things are growing right? Like with a little blackbox computer that only makes the 
 person who gave it to you pay when it's behaving right?\n\nLet's break this down in terms of decentralization, security, censorship, stocks, 
 fiat money, mining, equality, data patterns, the 2008 meltdown, and what scares my 5-year-old is afraid of losing.\n\n
 Vote Smarts.\n\nDon't be stupid.\n\nEnd the nonsense"
"""