#include "TP_ThirdPersonGameMode.h"

#include "GameFramework/PlayerStart.h"
#include "Kismet/GameplayStatics.h"
#include "NetDummy.h"
#include "NetTestPlayerController.h"
#include "UObject/ConstructorHelpers.h"

ATP_ThirdPersonGameMode::ATP_ThirdPersonGameMode()
{
	static ConstructorHelpers::FClassFinder<APawn> PlayerPawnBPClass(TEXT("/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter"));
	if (PlayerPawnBPClass.Class != nullptr)
	{
		DefaultPawnClass = PlayerPawnBPClass.Class;
	}
	PlayerControllerClass = ANetTestPlayerController::StaticClass();
}

void ATP_ThirdPersonGameMode::BeginPlay()
{
	Super::BeginPlay();
	const AActor* Start = UGameplayStatics::GetActorOfClass(GetWorld(), APlayerStart::StaticClass());
	const FTransform At = Start ? Start->GetActorTransform() : FTransform::Identity;
	FActorSpawnParameters Params;
	Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	GetWorld()->SpawnActor<ANetDummy>(ANetDummy::StaticClass(),
		FTransform(At.GetRotation(), At.GetLocation() + At.GetRotation().GetForwardVector() * 700.0), Params);
}
