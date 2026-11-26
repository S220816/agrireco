# agrireco
Un agriculteur dépose l'analyse de son sol, et le système lui dit automatiquement quelle culture planter , en moins d'une minute.

## Plateforme Azure (partie C)

### Choix et justification de la région

Région choisie : **francecentral** (Paris).

- Régions autorisées par la politique de notre abonnement Azure for Students : `francecentral`, `germanywestcentral`, `italynorth`, `austriaeast`, `switzerlandnorth`.
- - Les 5 apparaissent aussi dans `az functionapp list-flexconsumption-locations` (plan Flex Consumption disponible).
  - - Nous avons pris **francecentral** : la région la plus proche de nos utilisateurs, et les données restent en France.
    - - Validation : Cosmos DB (le service le plus souvent refusé) a été créé **en premier**, sans erreur, dans cette région.
      - - Région de secours prévue : `germanywestcentral`.
       
        - ### Ressources Azure
       
        - Toutes les ressources sont dans le groupe `rg-projet-agrinb27`, avec les étiquettes `projet=dscc` et `equipe=agrinb27`.
       
        - | Ressource | Nom | Rôle |
        - |---|---|---|
        - | Groupe de ressources | `rg-projet-agrinb27` | Contient tout le projet ; supprimé en une commande à la fin |
        - | Cosmos DB (Free Tier) | `cosmos-agrinb27` | Base `pipeline`, conteneur `inferences` (clé `/fileName`) : un document par prédiction |
        - | Compte de stockage (Standard_LRS) | `stagrinb27pipe` | Conteneurs `input`, `output`, `rejected`, `models` ; file `inference-requests` ; site statique `$web` ; versioning activé ; accès public désactivé |
        - | Log Analytics | `log-agrinb27` | Stockage des journaux, plafond 0,2 Go/jour |
        - | Application Insights | `appi-agrinb27` | Télémétrie de l'API et des fonctions, métriques, alerte |
        - | Environnement Container Apps | `cae-agrinb27` | Héberge l'API |
        - | Container App | `api-agrinb27` | API FastAPI : 0,25 vCPU, 0,5 Gio, 0 à 2 réplicas |
        - | Function App (Flex Consumption) | `func-agrinb27` | Les 3 fonctions : dispatcher, worker, recent (Python 3.12, 512 Mo) |
        - | Identité managée | `id-github-agrinb27` | Utilisée par GitHub Actions pour déployer (OIDC) |
       
        - ### Sécurité : aucun secret
       
        - - La Function App utilise son **identité managée** (aucune clé, aucune chaîne de connexion), avec les rôles **Storage Blob Data Owner**, **Storage Queue Data Contributor** (sur le compte de stockage uniquement) et **Cosmos DB Built-in Data Contributor**.
          - - Les réglages de la Function App ne contiennent que des **adresses** (`AGRI_STORAGE__blobServiceUri`, `AGRI_STORAGE__queueServiceUri`, `COSMOS_ENDPOINT`, `API_URL`, `QUEUE_NAME`, `COSMOS_DATABASE`, `COSMOS_CONTAINER`).
            - - GitHub Actions se connecte à Azure par **OIDC** (identité fédérée) : les seuls secrets GitHub sont `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` et `AZURE_SUBSCRIPTION_ID`, qui sont des identifiants, pas des mots de passe.
              - - L'identité GitHub a le rôle **Contributor limité au groupe de ressources** (moindre privilège).
                - - **CORS** de la Function App : seule l'adresse du site (`https://stagrinb27pipe.z28.web.core.windows.net`) est autorisée.
                 
                  - ### CI/CD
                 
                  - - **`ci.yml`** (à chaque push et pull request) : lint `ruff`, tests `pytest` de l'API, puis construction de l'image Docker et publication sur GitHub Container Registry, étiquetée avec la version (`api/VERSION`) et le SHA du commit.
                    - - **`deploy.yml`** (après un `ci` réussi sur `main`) : connexion OIDC à Azure, mise à jour de la Container App avec la nouvelle image, puis déploiement de la Function App. Le déploiement passe par l'environnement GitHub **`production`**, qui exige l'approbation d'un membre de l'équipe.
                      - - Note : la version récente de `ruff` active par défaut des règles de style ; nous limitons le lint aux erreurs réelles avec `--select E4,E7,E9,F`.
                       
                        - ### Supervision
                       
                        - - Application Insights, basé sur Log Analytics (plafond 0,2 Go/jour).
                          - - Métriques personnalisées de l'API : `inferences` (nombre de prédictions) et `model_latency_ms` (temps de calcul du modèle).
                            - - **Alerte** `alerte-erreurs-agrinb27` : plus de 5 requêtes en échec en 5 minutes → e-mail via le groupe d'actions `ag-agrinb27`.
                              - - Requêtes KQL : voir la section dédiée (à compléter avec les captures).
                               
                                - ### Restrictions Azure for Students rencontrées
                               
                                - | Problème | Solution |
                                - |---|---|
                                - | Seulement 5 régions autorisées par la politique de l'abonnement | Croisement avec les régions Flex Consumption → `francecentral` |
                                - | Une seule ressource Cosmos DB gratuite par abonnement, option impossible à ajouter après coup | Cosmos DB créé en premier avec `--enable-free-tier true` |
                                - | Cloud Shell déconnecté après inactivité (« Timeout waiting for token ») | Reconnexion avec `az login --identity` (la connexion par code est bloquée par l'école) |
                                - | Un seul environnement Container Apps autorisé | Un seul environnement `cae-agrinb27` pour tout le projet |
                               
                                - ### Coûts : estimation initiale
                               
                                - Estimation pour les 15 jours : **0 à 3 $**.
                               
                                - - Cosmos DB : gratuit (Free Tier, 400 RU/s).
                                  - - API (Container Apps) et fonctions (Flex Consumption) : descendent à 0 sans trafic.
                                    - - Stockage : quelques centimes (fichiers de petite taille).
                                      - - Log Analytics : plafonné à 0,2 Go/jour.
                                        - - Budget de **25 $** sur le groupe de ressources, avec alertes à 20, 50 et 80 %.
                                         
                                          - Le coût réel à J15 (capture de Cost Analysis filtrée sur `projet=dscc`) sera ajouté dans le rapport de coûts.
                                          -

## Partie B — pipeline (Sarah)

Dépôt d'un CSV dans `input/` → **Event Grid** (BlobCreated, filtré sur `input/`) → `dispatcher` → file `inference-requests` → `worker` (API de prédiction, Cosmos DB, `output/`) → `GET /api/recent` → tableau de bord. Échecs répétés → `inference-requests-poison`.

**Pourquoi le déclencheur Event Grid ?** L'événement contient le nom du blob, son ETag et sa taille, exactement ce que le message de la file doit porter, et l'abonnement se crée vers la fonction sans URL ni clé. L'abonnement est filtré sur `Microsoft.Storage.BlobCreated` et sur le dossier `input/`.

Détails, réglages et commandes Azure : [docs/PIPELINE.md](docs/PIPELINE.md).
